"""Race conditions between the recovery cycle and everything else. P1.

Each scenario here is a window where two things touch one invoice. The dangerous
outcome in almost every case is the same: contacting a customer who has already paid,
or contacting them twice.
"""

import json
import threading
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session as RawSession
from sqlmodel import select

from app.core.config import settings
from app.core.constants import InvoiceStatus, PromiseStatus
from app.core.db import engine
from app.integrations.razorpay_signature import compute_signature
from app.main import create_app
from app.models import Invoice, PaymentLink, Promise, ReconciliationEvent, Reminder
from app.services.recovery import CYCLE_LOCK_ID, run_recovery_cycle


@pytest.fixture
def api(session):
    with TestClient(create_app()) as c:
        c.headers.update({"X-Admin-Key": settings.admin_api_key})
        yield c


@pytest.fixture
def link(session, invoice) -> PaymentLink:
    pl = PaymentLink(
        invoice_id=invoice.id,
        razorpay_payment_link_id="plink_RACE1",
        reference_id=f"vsl-{invoice.invoice_number}",
        short_url="https://rzp.io/rzp/race1",
        amount_expected_paise=invoice.amount_paise,
    )
    session.add(pl)
    session.commit()
    session.refresh(pl)
    return pl


def cycle(session, **kw):
    kw.setdefault("use_llm", False)
    return run_recovery_cycle(session, **kw)


def post_payment(api, invoice, link, *, event_id="evt_race", amount=None):
    payload = {
        "entity": "event",
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": link.razorpay_payment_link_id,
                    "reference_id": link.reference_id,
                    "amount": invoice.amount_paise,
                    "amount_paid": amount or invoice.amount_paise,
                    "status": "paid",
                    "notes": {"invoice_id": str(invoice.id)},
                }
            }
        },
    }
    raw = json.dumps(payload).encode()
    return api.post(
        "/api/webhooks/razorpay",
        content=raw,
        headers={
            "X-Razorpay-Signature": compute_signature(raw, settings.razorpay_webhook_secret),
            "X-Razorpay-Event-Id": event_id,
            "Content-Type": "application/json",
        },
    )


# ===========================================================================
# 1. Payment arrives while the cycle is deciding.
# ===========================================================================


def test_a_payment_landing_mid_cycle_cancels_the_send(api, session, invoice, link, monkeypatch):
    """The window: diagnosis and drafting are network calls. A payment can land
    between the policy decision and the send."""
    import app.services.recovery as recovery_mod

    real_draft = recovery_mod.draft_reminder

    def draft_then_pay(*args, **kwargs):
        result = real_draft(*args, **kwargs)
        # The payment arrives exactly here, after policy approved the draft.
        post_payment(api, invoice, link)
        return result

    monkeypatch.setattr(recovery_mod, "draft_reminder", draft_then_pay)

    report = cycle(session)

    assert report.sent == 0, "must not chase a customer who just paid"
    assert session.exec(select(Reminder)).all() == []

    session.expire_all()
    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.RECOVERED


def test_a_recovered_invoice_is_never_picked_up_at_all(api, session, invoice, link):
    post_payment(api, invoice, link)
    session.expire_all()

    report = cycle(session)
    assert report.sent == 0
    assert session.exec(select(Reminder)).all() == []


# ===========================================================================
# 2. A promise arrives while the cycle is deciding.
# ===========================================================================


def test_a_promise_landing_mid_cycle_cancels_the_send(session, invoice, monkeypatch):
    import app.services.recovery as recovery_mod

    real_draft = recovery_mod.draft_reminder

    def draft_then_promise(*args, **kwargs):
        result = real_draft(*args, **kwargs)
        with RawSession(engine) as other:
            other.add(
                Promise(
                    invoice_id=invoice.id,
                    promised_date=date.today() + timedelta(days=10),
                    source_message_excerpt="paying soon",
                    extraction_confidence=0.9,
                    status=PromiseStatus.ACTIVE,
                    tier_at_pause=1,
                )
            )
            inv = other.get(Invoice, invoice.id)
            inv.status = InvoiceStatus.PROMISE_ACTIVE
            other.add(inv)
            other.commit()
        return result

    monkeypatch.setattr(recovery_mod, "draft_reminder", draft_then_promise)

    report = cycle(session)
    assert report.sent == 0
    assert session.exec(select(Reminder)).all() == []


# ===========================================================================
# 3. Two cycles at once.
# ===========================================================================


def test_a_second_cycle_cannot_run_while_one_is_running(session, invoice):
    """APScheduler's max_instances guards one process. A second Railway worker would
    not know about it, and both would approve the same send."""
    holder = RawSession(engine)
    try:
        acquired = bool(
            holder.exec(text("SELECT pg_try_advisory_lock(:k)").bindparams(k=CYCLE_LOCK_ID)).one()[
                0
            ]
        )
        assert acquired

        report = cycle(session)
        assert report.sent == 0
        assert any("already running" in e["error"] for e in report.errors)
    finally:
        holder.exec(text("SELECT pg_advisory_unlock_all()")).one()
        holder.close()


def test_the_cycle_lock_is_released_for_the_next_run(session, invoice):
    """The regression that mattered: the lock was taken on the session's pooled
    connection and released on a different one, so every later cycle silently no-opped."""
    for _ in range(3):
        report = cycle(session, dry_run=True)
        assert not any("already running" in e["error"] for e in report.errors)

    with RawSession(engine) as check:
        held = check.exec(text("SELECT count(*) FROM pg_locks WHERE locktype='advisory'")).one()[0]
    assert held == 0, "no advisory lock may outlive the cycle"


# ===========================================================================
# 4. Two webhook deliveries at once.
# ===========================================================================


def test_simultaneous_duplicate_webhooks_count_once(api, session, invoice, link):
    """The unique index is the dedup, not an in-memory set: it is atomic, survives a
    restart, and is shared across workers.

    Genuinely concurrent. An earlier version of this test issued six blocking requests
    from a list comprehension — sequential, despite the name — so it would have passed
    even if overlapping delivery were unsafe. Threads make the race real: the six
    requests are released together and the assertions below only hold if the database
    constraint, not request ordering, is what deduplicates.
    """
    import concurrent.futures

    barrier = threading.Barrier(6)

    def deliver():
        barrier.wait()  # release all six at the same instant
        return post_payment(api, invoice, link, event_id="evt_same")

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        responses = [f.result() for f in [pool.submit(deliver) for _ in range(6)]]

    statuses = [r.json()["status"] for r in responses]
    # Exactly one winner, whichever thread got there first.
    assert statuses.count("processed") == 1, statuses
    assert all(s in {"processed", "duplicate_ignored"} for s in statuses), statuses
    assert len(session.exec(select(ReconciliationEvent)).all()) == 1

    session.expire_all()
    session.refresh(invoice)
    assert invoice.amount_paid_paise == invoice.amount_paise


def test_distinct_events_for_the_same_invoice_do_not_double_count(api, session, invoice, link):
    """Two genuinely different events reporting the same running total."""
    post_payment(api, invoice, link, event_id="evt_a")
    post_payment(api, invoice, link, event_id="evt_b")

    session.expire_all()
    session.refresh(invoice)
    assert invoice.amount_paid_paise == invoice.amount_paise, "not doubled"


# ===========================================================================
# 5. Email retry racing the cycle.
# ===========================================================================


def test_a_retry_and_a_cycle_do_not_both_send_the_same_tier(session, invoice, live_email=None):
    """UNIQUE(invoice_id, tier) is the backstop if both paths ever try at once."""
    from sqlalchemy.exc import IntegrityError

    cycle(session)
    existing = session.exec(select(Reminder)).one()

    session.add(
        Reminder(
            invoice_id=invoice.id,
            tier=existing.tier,
            tone="firm",
            subject="duplicate",
            body="duplicate",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


# ===========================================================================
# 6. Payment immediately after a reminder is generated.
# ===========================================================================


def test_a_payment_just_after_sending_still_stops_the_next_tier(api, session, invoice, link):
    cycle(session)
    assert len(session.exec(select(Reminder)).all()) == 1

    post_payment(api, invoice, link)
    session.expire_all()

    report = cycle(session)
    assert report.sent == 0
    assert len(session.exec(select(Reminder)).all()) == 1
