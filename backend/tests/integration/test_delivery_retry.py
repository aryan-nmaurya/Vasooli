"""Email delivery attempts and retries. P0 reliability.

The bug these exist for: a reminder row was written whether or not delivery succeeded,
and the recovery cycle treated any row for a tier as proof that tier had been sent. A
single bounced email therefore consumed the tier permanently — the customer never
received a reminder, and the invoice was never chased again, with no error anywhere.
"""

import base64
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core.clock import utcnow
from app.core.constants import InvoiceStatus
from app.integrations.email.base import SendResult
from app.models import AuditAction, AuditLog, Reminder
from app.models.reminder import MAX_DELIVERY_ATTEMPTS
from app.services.messaging import retry_failed_deliveries
from app.services.recovery import run_recovery_cycle


class Mailer:
    """A provider whose outcome the test controls."""

    name = "test-provider"

    def __init__(self, *, fail_times: int = 0, error: str = "550 mailbox unavailable"):
        self.fail_times = fail_times
        self.error = error
        self.calls: list[dict] = []

    def send(self, **kw) -> SendResult:
        self.calls.append(kw)
        if self.fail_times > 0:
            self.fail_times -= 1
            return SendResult(sent=False, provider=self.name, error=self.error, retryable=True)
        return SendResult(sent=True, provider=self.name, message_id=f"msg_{len(self.calls)}")


@pytest.fixture
def live_email(monkeypatch):
    """Turn dry-run off so the provider is actually consulted.

    Dry-run always reports success, which would make every one of these tests pass
    regardless of the code under test.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "email_dry_run", False, raising=False)
    monkeypatch.setattr(settings, "email_redirect_to", "ops@example.invalid", raising=False)
    monkeypatch.setattr(settings, "email_reply_to_domain", "replies.example.test", raising=False)
    monkeypatch.setattr(
        settings,
        "resend_inbound_webhook_secret",
        "whsec_" + base64.b64encode(b"test-webhook-secret").decode(),
        raising=False,
    )


def cycle(session, provider=None, **kw):
    kw.setdefault("use_llm", False)
    if provider is not None:
        import app.services.messaging as messaging

        original = messaging.ResendProvider
        messaging.ResendProvider = lambda *a, **k: provider  # type: ignore[assignment]
        try:
            return run_recovery_cycle(session, **kw)
        finally:
            messaging.ResendProvider = original  # type: ignore[assignment]
    return run_recovery_cycle(session, **kw)


# ===========================================================================
# Success.
# ===========================================================================


def test_a_successful_send_counts_toward_the_cadence(session, invoice, live_email):
    mailer = Mailer()
    cycle(session, mailer)

    reminder = session.exec(select(Reminder)).one()
    assert reminder.sent_at is not None
    assert reminder.attempt_count == 1
    assert reminder.next_retry_at is None
    assert reminder.send_error is None
    assert reminder.delivery_state == "sent"
    assert reminder.lease_token is None

    session.refresh(invoice)
    assert invoice.reminders_sent == 1
    assert invoice.last_reminder_at is not None


# ===========================================================================
# Failure. The core bug.
# ===========================================================================


def test_a_failed_send_does_not_count_as_a_reminder(session, invoice, live_email):
    cycle(session, Mailer(fail_times=1))

    reminder = session.exec(select(Reminder)).one()
    assert reminder.sent_at is None
    assert reminder.attempt_count == 1
    assert "550" in (reminder.send_error or "")
    assert reminder.delivery_state == "failed"

    session.refresh(invoice)
    assert invoice.reminders_sent == 0, "a bounce must not consume a reminder"
    assert invoice.last_reminder_at is None, "cooldown must not start on a failed send"


def test_a_failed_send_schedules_a_retry(session, invoice, live_email):
    cycle(session, Mailer(fail_times=1))
    reminder = session.exec(select(Reminder)).one()
    assert reminder.next_retry_at is not None
    assert reminder.next_retry_at > utcnow()


def test_a_failed_tier_is_not_silently_skipped(session, invoice, live_email):
    """The strand, stated directly: the customer is still owed this tier."""
    cycle(session, Mailer(fail_times=1))
    reminder = session.exec(select(Reminder)).one()
    assert reminder.needs_retry is True


def test_the_failure_is_visible_in_the_audit_trail(session, invoice, live_email):
    cycle(session, Mailer(fail_times=1))
    entry = session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.REMINDER_FAILED)
    ).one()
    assert entry.detail["attempt"] == 1
    assert entry.detail["next_retry_at"] is not None
    assert entry.detail["exhausted"] is False


# ===========================================================================
# Retry.
# ===========================================================================


def _make_retry_due(session, reminder) -> None:
    reminder.next_retry_at = utcnow() - timedelta(seconds=1)
    session.add(reminder)
    session.commit()


def test_a_retry_after_a_failure_succeeds(session, invoice, live_email):
    cycle(session, Mailer(fail_times=1))
    reminder = session.exec(select(Reminder)).one()
    _make_retry_due(session, reminder)

    report = retry_failed_deliveries(session, provider=Mailer())
    assert report == {"attempted": 1, "recovered": 1, "still_failing": 0}

    session.refresh(reminder)
    assert reminder.sent_at is not None
    assert reminder.attempt_count == 2
    assert reminder.next_retry_at is None

    session.refresh(invoice)
    assert invoice.reminders_sent == 1, "counts only once it actually went out"


def test_a_retry_that_fails_again_backs_off_further(session, invoice, live_email):
    cycle(session, Mailer(fail_times=1))
    reminder = session.exec(select(Reminder)).one()
    first_gap = reminder.next_retry_at - utcnow()

    _make_retry_due(session, reminder)
    retry_failed_deliveries(session, provider=Mailer(fail_times=1))
    session.refresh(reminder)

    assert reminder.attempt_count == 2
    assert reminder.next_retry_at - utcnow() > first_gap, "backoff must widen"


def test_retries_stop_after_the_attempt_limit(session, invoice, live_email):
    """Bounded. An address that has bounced five times will not start working."""
    cycle(session, Mailer(fail_times=1))
    reminder = session.exec(select(Reminder)).one()

    for _ in range(MAX_DELIVERY_ATTEMPTS + 2):
        if reminder.next_retry_at is None:
            break
        _make_retry_due(session, reminder)
        retry_failed_deliveries(session, provider=Mailer(fail_times=1))
        session.refresh(reminder)

    assert reminder.attempt_count <= MAX_DELIVERY_ATTEMPTS
    assert reminder.next_retry_at is None
    assert reminder.needs_retry is False

    session.refresh(invoice)
    assert invoice.reminders_sent == 0


def test_a_retry_before_its_backoff_elapses_is_not_attempted(session, invoice, live_email):
    cycle(session, Mailer(fail_times=1))
    mailer = Mailer()
    assert retry_failed_deliveries(session, provider=mailer)["attempted"] == 0
    assert mailer.calls == []


def test_an_active_delivery_lease_cannot_be_double_claimed(session, invoice, live_email):
    cycle(session, Mailer(fail_times=1))
    reminder = session.exec(select(Reminder)).one()
    reminder.delivery_state = "processing"
    reminder.lease_token = "worker-one"
    reminder.lease_expires_at = utcnow() + timedelta(minutes=1)
    reminder.next_retry_at = utcnow() - timedelta(seconds=1)
    session.add(reminder)
    session.commit()

    mailer = Mailer()
    assert retry_failed_deliveries(session, provider=mailer)["attempted"] == 0
    assert mailer.calls == []


def test_an_expired_delivery_lease_is_recovered_after_a_crash(session, invoice, live_email):
    cycle(session, Mailer(fail_times=1))
    reminder = session.exec(select(Reminder)).one()
    reminder.delivery_state = "processing"
    reminder.lease_token = "crashed-worker"
    reminder.lease_expires_at = utcnow() - timedelta(seconds=1)
    # A processing lease is reclaimable when it expires even if the ordinary retry
    # backoff is later; otherwise a crash after claiming strands the row.
    reminder.next_retry_at = utcnow() + timedelta(hours=1)
    session.add(reminder)
    session.commit()

    report = retry_failed_deliveries(session, provider=Mailer())
    assert report["recovered"] == 1
    session.refresh(reminder)
    assert reminder.delivery_state == "sent"
    assert reminder.lease_token is None


def test_a_successful_reminder_is_never_retried(session, invoice, live_email):
    cycle(session, Mailer())
    mailer = Mailer()
    assert retry_failed_deliveries(session, provider=mailer)["attempted"] == 0
    assert mailer.calls == []


def test_a_retry_reuses_the_approved_message(session, invoice, live_email):
    """Not re-drafted: the customer is owed the copy policy already approved, and
    re-drafting would re-run the model and produce different words for one tier."""
    cycle(session, Mailer(fail_times=1))
    reminder = session.exec(select(Reminder)).one()
    original_body = reminder.body
    _make_retry_due(session, reminder)

    mailer = Mailer()
    retry_failed_deliveries(session, provider=mailer)
    assert mailer.calls[0]["text"] == original_body


def test_a_retry_creates_no_second_row(session, invoice, live_email):
    cycle(session, Mailer(fail_times=1))
    reminder = session.exec(select(Reminder)).one()
    _make_retry_due(session, reminder)
    retry_failed_deliveries(session, provider=Mailer())
    assert len(session.exec(select(Reminder)).all()) == 1


def test_a_retry_is_abandoned_once_the_invoice_is_paid(session, invoice, live_email):
    """Chasing someone who has already paid is the worst false positive here."""
    cycle(session, Mailer(fail_times=1))
    reminder = session.exec(select(Reminder)).one()
    _make_retry_due(session, reminder)

    invoice.status = InvoiceStatus.RECOVERED
    invoice.amount_paid_paise = invoice.amount_paise
    session.add(invoice)
    session.commit()

    mailer = Mailer()
    retry_failed_deliveries(session, provider=mailer)
    assert mailer.calls == []

    session.refresh(reminder)
    assert reminder.next_retry_at is None
    assert "abandoned" in (reminder.send_error or "")


def test_a_retry_is_abandoned_when_a_dispute_pauses_recovery(session, invoice, live_email):
    cycle(session, Mailer(fail_times=1))
    reminder = session.exec(select(Reminder)).one()
    _make_retry_due(session, reminder)

    invoice.status = InvoiceStatus.HUMAN_REVIEW
    invoice.escalation_reason = "complaint_in_reply"
    session.add(invoice)
    session.commit()

    mailer = Mailer()
    retry_failed_deliveries(session, provider=mailer)
    assert mailer.calls == []
    session.refresh(reminder)
    assert reminder.next_retry_at is None


# ===========================================================================
# The cycle must not route around a pending retry.
# ===========================================================================


def test_the_cycle_does_not_jump_to_the_next_tier_after_a_failure(session, invoice, live_email):
    """The tier that failed is still owed; the cycle must not move past it."""
    cycle(session, Mailer(fail_times=1))
    owed_tier = session.exec(select(Reminder)).one().tier

    # The backoff has not elapsed, so this cycle should do nothing at all.
    cycle(session, Mailer())

    reminders = session.exec(select(Reminder)).all()
    assert len(reminders) == 1, "must not draft a fresh reminder for another tier"
    assert reminders[0].tier == owed_tier
    assert reminders[0].sent_at is None

    session.refresh(invoice)
    assert invoice.reminders_sent == 0


def test_the_cycle_retries_pending_deliveries_before_new_tiers(session, invoice, live_email):
    cycle(session, Mailer(fail_times=1))
    reminder = session.exec(select(Reminder)).one()
    _make_retry_due(session, reminder)

    report = cycle(session, Mailer())
    assert report.deliveries_retried == 1
    assert report.deliveries_recovered == 1

    session.refresh(invoice)
    assert invoice.reminders_sent == 1


# ===========================================================================
# State changes between drafting and delivery. Audit findings 3 and 4.
# ===========================================================================


def _failed_reminder(session, invoice):
    """One reminder that failed its first delivery and is due for retry."""
    cycle(session, Mailer(fail_times=1))
    reminder = session.exec(select(Reminder)).one()
    _make_retry_due(session, reminder)
    return reminder


def test_a_promise_stops_a_queued_retry(session, invoice, live_email):
    """The bug this pins: "a promise pauses escalation" held on the cadence path and
    not on the retry path, so a failed Tier 1 reminder went out minutes after the
    customer promised to pay.
    """
    from app.services.replies import handle_reply

    _failed_reminder(session, invoice)
    # The shared fixture runs the cadence to Tier 3, which hands over. Put the invoice
    # back in the cadence first: the scenario being pinned is an earlier tier failing
    # delivery while the invoice is still being chased, then the customer promising.
    invoice.status = InvoiceStatus.CHASING
    invoice.escalation_reason = None
    invoice.escalated_to_human_at = None
    session.add(invoice)
    session.commit()

    handle_reply(session, invoice, "Cash is tight — I'll clear this by the 28th.", use_llm=False)
    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.PROMISE_ACTIVE

    mailer = Mailer()
    retry_failed_deliveries(session, provider=mailer)
    assert mailer.calls == []


def test_a_dispute_stops_a_queued_retry(session, invoice, live_email):
    from app.services.replies import handle_reply

    _failed_reminder(session, invoice)
    invoice.status = InvoiceStatus.CHASING
    invoice.escalation_reason = None
    session.add(invoice)
    session.commit()

    handle_reply(
        session,
        invoice,
        "We were billed for 12 units but only received 9.",
        use_llm=False,
    )

    mailer = Mailer()
    retry_failed_deliveries(session, provider=mailer)
    assert mailer.calls == []


def test_a_manual_escalation_stops_a_queued_retry(session, invoice, live_email):
    """An operator taking an invoice out of automation must be respected by the
    retry worker, not only by the cycle."""
    from app.services.recovery import escalate_to_human

    _failed_reminder(session, invoice)
    invoice.status = InvoiceStatus.CHASING
    invoice.escalation_reason = None
    session.add(invoice)
    session.commit()

    escalate_to_human(session, invoice, "manual")
    session.commit()

    mailer = Mailer()
    retry_failed_deliveries(session, provider=mailer)
    assert mailer.calls == []


def test_tier_three_handover_does_not_strand_its_own_reminder(session, invoice, live_email):
    """The final notice must survive a failed first attempt, and the handover must
    follow the message rather than precede it.

    A Tier 3 attempt that failed has told the customer nothing, so the invoice stays in
    the cadence and the queued retry is free to run. Only once the message has actually
    been delivered does the invoice hand over to a human — "tier_3_reached" is rendered
    to operators as "all three automated reminders have been sent", so claiming it for
    a send that never left is a lie the dashboard repeats.
    """
    _failed_reminder(session, invoice)
    session.refresh(invoice)
    assert invoice.status != InvoiceStatus.HUMAN_REVIEW
    assert invoice.escalation_reason != "tier_3_reached"

    mailer = Mailer()
    retry_failed_deliveries(session, provider=mailer)
    assert len(mailer.calls) == 1

    # The retry landed, so now the handover is owed.
    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.HUMAN_REVIEW
    assert invoice.escalation_reason == "tier_3_reached"


def test_payment_landing_after_the_lease_is_still_caught(session, invoice, live_email):
    """Finding 4: the checks ran, the lease committed, and only then did the provider
    call happen — leaving a window where reconciliation could mark the invoice paid
    and the reminder would still be delivered.
    """
    reminder = _failed_reminder(session, invoice)

    class PaysMidFlight(Mailer):
        """Simulates the webhook landing inside the send window."""

        def send(self, **kwargs):  # noqa: ANN003
            raise AssertionError("must not reach the provider")

    # Money lands after the reminder was queued and deemed sendable.
    invoice.amount_paid_paise = invoice.amount_paise
    invoice.status = InvoiceStatus.RECOVERED
    session.add(invoice)
    session.commit()

    mailer = PaysMidFlight()
    retry_failed_deliveries(session, provider=mailer)

    session.refresh(reminder)
    assert reminder.sent_at is None
    assert reminder.delivery_state == "dead"
