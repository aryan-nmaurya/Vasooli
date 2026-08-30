"""Three operational blind spots the audit named, and the evidence that closes them.

* Email "delivery" was an API acceptance. A hard bounce reached nobody.
* A customer reply that failed to process was a dead end — the webhook had already
  answered 200, so the provider never sent it again and nothing retried it.
* "The scheduler is enabled" is a fact about configuration, not about execution.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.config import settings
from app.core.constants import InvoiceStatus, Tone
from app.main import create_app
from app.models import (
    AuditAction,
    AuditLog,
    EmailEvent,
    InboundMessage,
    JobRun,
    JobStatus,
    Reminder,
)
from app.models.inbound_message import MAX_INBOUND_ATTEMPTS
from app.services.automation import automation_health, record_run
from app.services.replies import mark_inbound_failed, retry_failed_inbound


@pytest.fixture
def api(session):
    with TestClient(create_app()) as c:
        c.headers.update({"X-Admin-Key": settings.admin_api_key})
        yield c


@pytest.fixture
def sent_reminder(session, invoice) -> Reminder:
    reminder = Reminder(
        invoice_id=invoice.id,
        tier=1,
        tone=Tone.POLITE,
        subject="Invoice reminder",
        body="Please pay.",
        provider="resend",
        provider_message_id="msg_abc123",
        sent_at=datetime.now(UTC),
        delivery_state="sent",
        attempt_count=1,
    )
    session.add(reminder)
    session.commit()
    session.refresh(reminder)
    return reminder


def delivery_event(event_type: str, *, message_id="msg_abc123", bounce=None, at=None) -> dict:
    return {
        "type": event_type,
        "created_at": (at or datetime.now(UTC)).isoformat(),
        "data": {
            "email_id": message_id,
            "to": ["abc@example.com"],
            **({"bounce": bounce} if bounce else {}),
        },
    }


def post_delivery(api, monkeypatch, event: dict, *, event_id: str):
    monkeypatch.setattr("app.api.webhooks.verify_webhook", lambda *_a, **_k: event)
    return api.post("/api/webhooks/resend/delivery", content=b"{}", headers={"svix-id": event_id})


# ===========================================================================
# Delivery is not the same fact as acceptance.
# ===========================================================================


def test_a_sent_reminder_is_not_yet_a_delivered_one(sent_reminder):
    assert sent_reminder.was_sent is True
    assert sent_reminder.reached_the_customer is False


def test_a_delivered_event_records_delivery(api, session, monkeypatch, sent_reminder):
    response = post_delivery(api, monkeypatch, delivery_event("email.delivered"), event_id="evt_d1")
    assert response.json()["status"] == "applied"

    session.refresh(sent_reminder)
    assert sent_reminder.reached_the_customer is True
    assert sent_reminder.delivery_status == "delivered"


def test_a_hard_bounce_stops_automation_for_that_invoice(
    api, session, monkeypatch, sent_reminder, invoice
):
    """The failure mode this exists to prevent: every reminder bounces, the invoice
    advances through the tiers anyway, and it is escalated as an unresponsive
    customer who never received a word."""
    invoice.status = InvoiceStatus.CHASING
    session.add(invoice)
    session.commit()

    post_delivery(
        api,
        monkeypatch,
        delivery_event("email.bounced", bounce={"message": "Mailbox does not exist"}),
        event_id="evt_b1",
    )

    session.refresh(sent_reminder)
    session.refresh(invoice)
    assert sent_reminder.hard_failed is True
    assert sent_reminder.delivery_detail == "Mailbox does not exist"
    assert invoice.status == InvoiceStatus.HUMAN_REVIEW
    assert invoice.escalation_reason == "email_bounced"

    actions = {row.action for row in session.exec(AuditLog.__table__.select()).all()}
    assert AuditAction.CONTACT_SUPPRESSED in actions


def test_a_spam_complaint_also_stops_automation(api, session, monkeypatch, sent_reminder, invoice):
    invoice.status = InvoiceStatus.CHASING
    session.add(invoice)
    session.commit()

    post_delivery(api, monkeypatch, delivery_event("email.complained"), event_id="evt_c1")

    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.HUMAN_REVIEW
    assert invoice.escalation_reason == "recipient_marked_as_spam"


def test_a_soft_delay_does_not_stop_anything(api, session, monkeypatch, sent_reminder, invoice):
    """Greylisting and a full mailbox clear on their own. Escalating on one would hand
    a human every invoice whose recipient was briefly slow."""
    invoice.status = InvoiceStatus.CHASING
    session.add(invoice)
    session.commit()

    post_delivery(api, monkeypatch, delivery_event("email.delivery_delayed"), event_id="evt_x1")

    session.refresh(invoice)
    session.refresh(sent_reminder)
    assert invoice.status == InvoiceStatus.CHASING
    assert sent_reminder.delivery_status == "deferred"
    assert sent_reminder.hard_failed is False


def test_a_redelivered_provider_event_changes_nothing(api, session, monkeypatch, sent_reminder):
    event = delivery_event("email.delivered")
    assert post_delivery(api, monkeypatch, event, event_id="evt_dupe").json()["status"] == "applied"
    second = post_delivery(api, monkeypatch, event, event_id="evt_dupe")
    assert second.json()["status"] == "duplicate_ignored"
    assert len(session.exec(select(EmailEvent)).all()) == 1


def test_an_older_event_never_overwrites_a_newer_one(api, session, monkeypatch, sent_reminder):
    """Providers do not guarantee order. A late `delivered` must not erase a bounce."""
    now = datetime.now(UTC)
    post_delivery(
        api,
        monkeypatch,
        delivery_event("email.bounced", bounce={"message": "refused"}, at=now),
        event_id="evt_late_bounce",
    )
    post_delivery(
        api,
        monkeypatch,
        delivery_event("email.delivered", at=now - timedelta(minutes=5)),
        event_id="evt_early_delivered",
    )

    session.refresh(sent_reminder)
    assert sent_reminder.delivery_status == "bounced"


def test_an_untracked_event_is_stored_but_changes_nothing(api, session, monkeypatch, sent_reminder):
    """An open is not evidence about delivery, and acting on one would be tracking."""
    response = post_delivery(api, monkeypatch, delivery_event("email.opened"), event_id="evt_o1")
    assert response.json()["status"] == "recorded"
    session.refresh(sent_reminder)
    assert sent_reminder.delivery_status is None
    assert len(session.exec(select(EmailEvent)).all()) == 1


def test_a_bounce_for_an_unknown_message_is_kept_not_dropped(api, session, monkeypatch):
    response = post_delivery(
        api, monkeypatch, delivery_event("email.bounced", message_id="msg_unknown"), event_id="e9"
    )
    assert response.json()["status"] == "unmatched"
    assert len(session.exec(select(EmailEvent)).all()) == 1


def test_an_unsigned_delivery_event_is_refused(api, monkeypatch):
    def invalid(*_a, **_k):
        raise ValueError("bad signature")

    monkeypatch.setattr("app.api.webhooks.verify_webhook", invalid)
    response = api.post("/api/webhooks/resend/delivery", content=b"{}", headers={"svix-id": "x"})
    assert response.status_code == 400


# ===========================================================================
# Inbound messages that failed to process are no longer a dead end.
# ===========================================================================


@pytest.fixture
def failed_inbound(session, invoice) -> InboundMessage:
    message = InboundMessage(
        invoice_id=invoice.id,
        provider_event_id="evt_in_1",
        message_id="<cust-1@example.com>",
        sender="abc@example.com",
        recipient="invoice-x@replies.example.com",
        subject="Re: invoice",
        body_text="We paid this on the 14th, please check.",
        signature_verified=True,
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    mark_inbound_failed(session, message, "RuntimeError: extractor exploded")
    session.refresh(message)
    return message


def test_a_failed_reply_is_scheduled_for_retry_rather_than_lost(failed_inbound):
    assert failed_inbound.processing_attempts == 1
    assert failed_inbound.next_retry_at is not None
    assert failed_inbound.needs_retry is True


def test_the_sweep_reprocesses_a_due_message(session, failed_inbound, monkeypatch):
    monkeypatch.setattr("app.services.replies.handle_reply", lambda *_a, **_k: None)
    failed_inbound.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    session.add(failed_inbound)
    session.commit()

    report = retry_failed_inbound(session)
    session.refresh(failed_inbound)
    assert report == {"attempted": 1, "recovered": 1}
    assert failed_inbound.processed_at is not None
    assert failed_inbound.processing_error is None


def test_the_sweep_leaves_a_message_whose_backoff_has_not_elapsed(session, failed_inbound):
    assert retry_failed_inbound(session) == {"attempted": 0, "recovered": 0}


def test_attempts_are_bounded_and_then_handed_to_a_person(session, failed_inbound):
    for _ in range(MAX_INBOUND_ATTEMPTS - 1):
        mark_inbound_failed(session, failed_inbound, "still broken")
    session.refresh(failed_inbound)
    assert failed_inbound.is_exhausted is True
    assert failed_inbound.next_retry_at is None
    assert failed_inbound.needs_retry is False


def test_a_stuck_reply_appears_in_the_exceptions_queue(api, failed_inbound):
    queue = api.get("/api/dashboard/exceptions").json()
    assert len(queue["inbound"]) == 1
    assert queue["inbound"][0]["error"].startswith("RuntimeError")
    assert queue["total"] >= 1


def test_an_operator_can_reprocess_an_exhausted_message(api, session, failed_inbound, monkeypatch):
    """The whole point of a manual retry: it ignores both the backoff and the cap."""
    for _ in range(MAX_INBOUND_ATTEMPTS):
        mark_inbound_failed(session, failed_inbound, "broken")
    session.refresh(failed_inbound)
    assert failed_inbound.is_exhausted

    monkeypatch.setattr("app.services.replies.handle_reply", lambda *_a, **_k: None)
    response = api.post(f"/api/dashboard/exceptions/inbound/{failed_inbound.id}/retry")

    assert response.status_code == 200
    assert response.json()["recovered"] is True
    session.refresh(failed_inbound)
    assert failed_inbound.processed_at is not None


def test_reprocessing_does_not_double_count_the_reply_history(
    api, session, failed_inbound, invoice, monkeypatch
):
    """handle_reply commits mid-way, so an attempt can leave the reply recorded and
    still fail. reply_count decides whether a customer is 'unresponsive'."""
    from app.services.replies import handle_reply

    monkeypatch.setattr(
        "app.services.replies.handle_reply",
        lambda s, i, b, **kw: handle_reply(s, i, b, use_llm=False, **kw),
    )
    api.post(f"/api/dashboard/exceptions/inbound/{failed_inbound.id}/retry")
    session.refresh(invoice)
    first = invoice.reply_count

    # A second reprocess of the same message must not add another reply.
    failed_inbound.processed_at = None
    session.add(failed_inbound)
    session.commit()
    api.post(f"/api/dashboard/exceptions/inbound/{failed_inbound.id}/retry")

    session.refresh(invoice)
    assert invoice.reply_count == first


# ===========================================================================
# Scheduler evidence.
# ===========================================================================


def test_a_successful_run_is_recorded_with_its_report(session):
    with record_run("recovery_cycle") as detail:
        detail.update(considered=4, sent=2)

    run = session.exec(select(JobRun).where(JobRun.job_id == "recovery_cycle")).one()
    assert run.status == JobStatus.SUCCEEDED
    assert run.finished_at is not None
    assert run.detail == {"considered": 4, "sent": 2}


def test_a_failing_run_is_recorded_and_the_error_still_propagates(session):
    with pytest.raises(RuntimeError), record_run("recovery_cycle"):
        raise RuntimeError("database gone")

    run = session.exec(select(JobRun).where(JobRun.job_id == "recovery_cycle")).one()
    assert run.status == JobStatus.FAILED
    assert "database gone" in run.error


def test_health_reports_healthy_after_a_recent_success(session, monkeypatch):
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    for job_id in ("recovery_cycle", "payment_link_sync", "retry_operations", "service_heartbeat"):
        with record_run(job_id):
            pass

    health = automation_health(session)
    assert health["overall"] == "healthy"
    assert {job["job_id"] for job in health["jobs"]} == {
        "recovery_cycle",
        "payment_link_sync",
        "retry_operations",
        "service_heartbeat",
    }


def test_a_stopped_job_is_reported_as_stale_not_healthy(session, monkeypatch):
    """The failure the whole feature exists for: the process is up, the API is
    healthy, and no cycle has run for days."""
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    with record_run("recovery_cycle"):
        pass
    run = session.exec(select(JobRun)).one()
    run.started_at = datetime.now(UTC) - timedelta(days=4)
    session.add(run)
    session.commit()

    health = automation_health(session)
    recovery = next(j for j in health["jobs"] if j["job_id"] == "recovery_cycle")
    assert recovery["state"] == "stale"
    assert health["overall"] in {"stale", "failing", "unknown"}


def test_a_failing_job_makes_the_whole_verdict_failing(session, monkeypatch):
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    with record_run("payment_link_sync"):
        pass
    with pytest.raises(RuntimeError), record_run("payment_link_sync"):
        raise RuntimeError("razorpay unreachable")

    health = automation_health(session)
    assert health["overall"] == "failing"


def test_no_history_reads_as_unknown_not_broken(session, monkeypatch):
    """A fresh deploy must not raise a false alarm on first boot."""
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    health = automation_health(session)
    assert all(job["state"] == "unknown" for job in health["jobs"])


def test_the_endpoint_is_gated(session):
    with TestClient(create_app()) as client:
        assert client.get("/api/dashboard/automation").status_code == 401


def test_the_endpoint_returns_the_verdict(api, session, monkeypatch):
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    with record_run("recovery_cycle") as detail:
        detail.update(sent=3)

    body = api.get("/api/dashboard/automation").json()
    assert body["scheduler_enabled"] is True
    recovery = next(j for j in body["jobs"] if j["job_id"] == "recovery_cycle")
    assert recovery["state"] == "healthy"
    assert recovery["last_detail"] == {"sent": 3}


# ===========================================================================
# Reviewer access: a way through the login wall that cannot write.
# ===========================================================================


def test_reviewer_access_is_absent_unless_enabled(session):
    with TestClient(create_app()) as client:
        assert client.get("/api/auth/modes").json()["reviewer_access"] is False
        assert client.post("/api/auth/reviewer").status_code == 404


def test_a_reviewer_session_can_read_but_not_write(session, operator_account, monkeypatch):
    monkeypatch.setattr(settings, "reviewer_access_enabled", True)
    monkeypatch.setattr(settings, "reviewer_username", operator_account.username)
    operator_account.role = "auditor"
    session.add(operator_account)
    session.commit()

    with TestClient(create_app()) as client:
        assert client.get("/api/auth/modes").json()["reviewer_access"] is True
        assert client.post("/api/auth/reviewer").status_code == 200
        assert client.get("/api/dashboard/queue").status_code == 200
        # Read-only is the role check in app.api.deps, not a promise made at login.
        assert client.post("/api/invoices/provision-batch").status_code == 403


def test_reviewer_access_fails_closed_on_a_non_auditor_account(
    session, operator_account, monkeypatch
):
    """A mistyped REVIEWER_USERNAME must not hand a stranger write access."""
    monkeypatch.setattr(settings, "reviewer_access_enabled", True)
    monkeypatch.setattr(settings, "reviewer_username", operator_account.username)
    assert operator_account.role == "admin"

    with TestClient(create_app()) as client:
        assert client.post("/api/auth/reviewer").status_code == 503


def test_a_delayed_bounce_undoes_an_earlier_delivery_confirmation(
    api, session, monkeypatch, sent_reminder, invoice
):
    """A message can be accepted by a receiving server and bounce minutes later.

    Both timestamps end up set, and reading only `delivered_at` would report that a
    reminder reached a customer who never saw it — exactly the false reassurance this
    change exists to remove.
    """
    invoice.status = InvoiceStatus.CHASING
    session.add(invoice)
    session.commit()

    now = datetime.now(UTC)
    post_delivery(api, monkeypatch, delivery_event("email.delivered", at=now), event_id="evt_seq_1")
    session.refresh(sent_reminder)
    assert sent_reminder.reached_the_customer is True

    post_delivery(
        api,
        monkeypatch,
        delivery_event(
            "email.bounced", bounce={"message": "Recipient rejected"}, at=now + timedelta(minutes=2)
        ),
        event_id="evt_seq_2",
    )

    session.refresh(sent_reminder)
    assert sent_reminder.hard_failed is True
    assert sent_reminder.reached_the_customer is False
    assert sent_reminder.delivered_at is not None  # the history is kept, not erased
