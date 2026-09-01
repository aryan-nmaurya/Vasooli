"""The recovery cycle. Doc §3.

The cycle is where every earlier phase meets: diagnosis picks a category, policy
approves or refuses, messaging records the send, and the cadence counters advance.
These tests run it end to end against a real schema with the model disabled, so they
exercise the deterministic path that has to work when quota runs out.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlmodel import select

from app.core.config import settings
from app.core.constants import (
    MAX_AUTOMATED_REMINDERS,
    TIER_1_DAYS_OVERDUE,
    TIER_2_DAYS_OVERDUE,
    TIER_3_DAYS_OVERDUE,
    InvoiceStatus,
    PromiseStatus,
    ReasonCategory,
)
from app.models import AuditAction, AuditLog, Invoice, Promise, Reminder
from app.services import recovery as recovery_module
from app.services.recovery import run_recovery_cycle


@pytest.fixture
def clock(monkeypatch):
    """Move 'now' without waiting three weeks.

    Everything reads time through app.core.clock, so shifting the offset moves the
    whole system consistently — which is the only reason a 21-day cadence is
    demonstrable at all.
    """

    class Clock:
        def advance_to_day(self, days: int, *, base_overdue: int) -> None:
            monkeypatch.setattr(
                settings, "demo_time_offset_days", days - base_overdue, raising=False
            )

    return Clock()


def make_invoice(session, merchant, customer, *, overdue: int, number="INV-C1", **kw) -> Invoice:
    due = datetime.now(UTC) - timedelta(days=overdue)
    invoice = Invoice(
        merchant_id=merchant.id,
        customer_id=customer.id,
        invoice_number=number,
        amount_paise=2_500_000,
        issued_at=due - timedelta(days=30),
        due_at=due,
        status=InvoiceStatus.CHASING,
        **kw,
    )
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    return invoice


def cycle(session, **kw):
    kw.setdefault("use_llm", False)
    return run_recovery_cycle(session, **kw)


# ===========================================================================
# Tier selection.
# ===========================================================================


def test_an_invoice_below_tier_1_is_not_contacted(session, merchant, customer):
    make_invoice(session, merchant, customer, overdue=TIER_1_DAYS_OVERDUE - 1)
    report = cycle(session)
    assert report.sent == 0
    assert report.skipped_no_tier == 1


def test_tier_1_fires_exactly_on_day_3(session, merchant, customer):
    make_invoice(session, merchant, customer, overdue=TIER_1_DAYS_OVERDUE)
    assert cycle(session).sent == 1
    assert session.exec(select(Reminder)).one().tier == 1


def test_an_invoice_found_late_goes_straight_to_the_right_tier(session, merchant, customer):
    """Ingested at day 25, it should not walk politely up from Tier 1 three weeks late."""
    make_invoice(session, merchant, customer, overdue=25)
    cycle(session)
    assert session.exec(select(Reminder)).one().tier == 3


# ===========================================================================
# The full cadence, via the demo clock. Doc §3 Stage 3.
# ===========================================================================


def test_the_cadence_walks_3_then_10_then_21_and_hands_over(session, merchant, customer, clock):
    invoice = make_invoice(session, merchant, customer, overdue=TIER_1_DAYS_OVERDUE)

    cycle(session)
    assert {r.tier for r in session.exec(select(Reminder)).all()} == {1}

    clock.advance_to_day(TIER_2_DAYS_OVERDUE, base_overdue=TIER_1_DAYS_OVERDUE)
    cycle(session)
    assert {r.tier for r in session.exec(select(Reminder)).all()} == {1, 2}

    clock.advance_to_day(TIER_3_DAYS_OVERDUE, base_overdue=TIER_1_DAYS_OVERDUE)
    cycle(session)
    assert {r.tier for r in session.exec(select(Reminder)).all()} == {1, 2, 3}

    session.refresh(invoice)
    assert invoice.reminders_sent == MAX_AUTOMATED_REMINDERS
    assert invoice.status == InvoiceStatus.HUMAN_REVIEW
    assert invoice.escalation_reason == "tier_3_reached"


def test_the_cap_holds_however_long_the_invoice_ages(session, merchant, customer, clock):
    """Doc §3: never fully autonomous indefinitely."""
    invoice = make_invoice(session, merchant, customer, overdue=TIER_1_DAYS_OVERDUE)
    for day in (TIER_1_DAYS_OVERDUE, TIER_2_DAYS_OVERDUE, TIER_3_DAYS_OVERDUE, 40, 90, 200):
        clock.advance_to_day(day, base_overdue=TIER_1_DAYS_OVERDUE)
        cycle(session)

    session.refresh(invoice)
    assert invoice.reminders_sent <= MAX_AUTOMATED_REMINDERS
    assert len(session.exec(select(Reminder)).all()) <= MAX_AUTOMATED_REMINDERS


def test_running_twice_on_the_same_day_sends_once(session, merchant, customer):
    make_invoice(session, merchant, customer, overdue=TIER_1_DAYS_OVERDUE)
    assert cycle(session).sent == 1
    assert cycle(session).sent == 0
    assert len(session.exec(select(Reminder)).all()) == 1


def test_cooldown_blocks_a_tier_that_is_due_too_soon(session, merchant, customer, clock):
    """Tier 2 is due on day 10, but a Tier 1 sent on day 6 makes that same-week contact."""
    make_invoice(session, merchant, customer, overdue=6)
    cycle(session)  # Tier 1 on day 6
    clock.advance_to_day(TIER_2_DAYS_OVERDUE, base_overdue=6)
    report = cycle(session)
    assert report.sent == 0
    assert report.held == 1


# ===========================================================================
# Dispute routing. Doc §3 Stage 2.
# ===========================================================================


def test_a_disputed_invoice_is_never_contacted(session, merchant, customer, clock):
    invoice = make_invoice(
        session, merchant, customer, overdue=TIER_1_DAYS_OVERDUE, has_prior_dispute_note=True
    )
    for day in (TIER_1_DAYS_OVERDUE, TIER_2_DAYS_OVERDUE, TIER_3_DAYS_OVERDUE, 60):
        clock.advance_to_day(day, base_overdue=TIER_1_DAYS_OVERDUE)
        cycle(session)

    assert session.exec(select(Reminder)).all() == []
    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.HUMAN_REVIEW
    assert invoice.reason_category == ReasonCategory.DISPUTE_LIKELY
    assert invoice.escalation_reason == "dispute_likely"


# ===========================================================================
# Promises. Doc §3 Stage 4.
# ===========================================================================


def test_an_active_promise_pauses_the_chase(session, merchant, customer, clock):
    invoice = make_invoice(session, merchant, customer, overdue=TIER_1_DAYS_OVERDUE)
    cycle(session)

    session.add(
        Promise(
            invoice_id=invoice.id,
            promised_date=date.today() + timedelta(days=20),
            source_message_excerpt="paying soon",
            extraction_confidence=0.9,
            tier_at_pause=1,
        )
    )
    session.commit()

    clock.advance_to_day(TIER_2_DAYS_OVERDUE, base_overdue=TIER_1_DAYS_OVERDUE)
    report = cycle(session)
    assert report.sent == 0
    assert report.held == 1


def test_a_broken_promise_resumes_at_the_paused_tier(session, merchant, customer, clock):
    """Not reset to polite. Someone who promised at Tier 2 and did not pay should not
    receive a friendly first nudge as though nothing happened."""
    invoice = make_invoice(session, merchant, customer, overdue=TIER_2_DAYS_OVERDUE)
    invoice.current_tier = 2
    invoice.reminders_sent = 2
    session.add(invoice)
    session.add(Reminder(invoice_id=invoice.id, tier=1, tone="polite", subject="s", body="b"))
    session.add(Reminder(invoice_id=invoice.id, tier=2, tone="firm", subject="s", body="b"))
    session.add(
        Promise(
            invoice_id=invoice.id,
            promised_date=date.today() - timedelta(days=5),
            source_message_excerpt="I'll pay",
            extraction_confidence=0.9,
            tier_at_pause=2,
        )
    )
    session.commit()

    report = cycle(session)
    assert report.promises_broken == 1

    session.refresh(invoice)
    assert invoice.current_tier == 2, "must resume where it paused, not at tier 1"

    promise = session.exec(select(Promise)).one()
    assert promise.status == PromiseStatus.BROKEN
    assert promise.resolved_at is not None

    session.refresh(customer)
    assert customer.broken_promises == 1  # feeds future diagnoses


def test_a_promise_kept_by_payment_is_not_marked_broken(session, merchant, customer):
    invoice = make_invoice(session, merchant, customer, overdue=TIER_2_DAYS_OVERDUE)
    invoice.amount_paid_paise = invoice.amount_paise
    invoice.status = InvoiceStatus.RECOVERED
    session.add(invoice)
    session.add(
        Promise(
            invoice_id=invoice.id,
            promised_date=date.today() - timedelta(days=5),
            source_message_excerpt="paid",
            extraction_confidence=0.9,
            tier_at_pause=2,
        )
    )
    session.commit()

    assert cycle(session).promises_broken == 0


# ===========================================================================
# Settled invoices.
# ===========================================================================


def test_a_recovered_invoice_is_never_chased(session, merchant, customer):
    invoice = make_invoice(session, merchant, customer, overdue=30)
    invoice.status = InvoiceStatus.RECOVERED
    invoice.amount_paid_paise = invoice.amount_paise
    session.add(invoice)
    session.commit()

    assert cycle(session).sent == 0
    assert session.exec(select(Reminder)).all() == []


def test_a_partially_paid_invoice_is_still_chased(session, merchant, customer):
    invoice = make_invoice(session, merchant, customer, overdue=TIER_1_DAYS_OVERDUE)
    invoice.amount_paid_paise = 500_000
    invoice.status = InvoiceStatus.PARTIALLY_PAID
    session.add(invoice)
    session.commit()

    assert cycle(session).sent == 1


# ===========================================================================
# Dry run and isolation.
# ===========================================================================


def test_dry_run_changes_nothing(session, merchant, customer):
    invoice = make_invoice(session, merchant, customer, overdue=TIER_1_DAYS_OVERDUE)
    report = cycle(session, dry_run=True)

    assert report.sent == 1  # would have sent
    assert session.exec(select(Reminder)).all() == []
    session.refresh(invoice)
    assert invoice.reminders_sent == 0
    assert invoice.last_reminder_at is None


def test_one_bad_invoice_does_not_stop_the_cycle(session, merchant, customer, monkeypatch):
    """A cycle that aborts halfway leaves the ledger in a state nobody can reason about."""
    make_invoice(session, merchant, customer, overdue=5, number="INV-GOOD")
    make_invoice(session, merchant, customer, overdue=5, number="INV-BAD")

    real_deliver = recovery_module.deliver_reminder

    def explode(session_, *, invoice, **kw):
        if invoice.invoice_number == "INV-BAD":
            raise RuntimeError("provider exploded")
        return real_deliver(session_, invoice=invoice, **kw)

    monkeypatch.setattr(recovery_module, "deliver_reminder", explode)

    report = cycle(session)
    assert report.sent == 1
    assert len(report.errors) == 1
    assert report.errors[0]["invoice_number"] == "INV-BAD"
    assert {r.invoice_id for r in session.exec(select(Reminder)).all()}


def test_a_second_cycle_cannot_run_concurrently(session, merchant, customer):
    """APScheduler's max_instances only guards one process; a second Railway worker
    would happily run a parallel cycle and both would approve the same send.

    Uses pg_try_advisory_lock, not pg_advisory_lock: the blocking variant waits
    forever if a previous run died holding the lock, which turns a failing test into
    a hung test suite.
    """
    from sqlalchemy import text
    from sqlmodel import Session as RawSession

    from app.core.db import engine
    from app.services.recovery import CYCLE_LOCK_ID

    make_invoice(session, merchant, customer, overdue=TIER_1_DAYS_OVERDUE)

    holder = RawSession(engine)
    try:
        acquired = bool(
            holder.exec(text("SELECT pg_try_advisory_lock(:k)").bindparams(k=CYCLE_LOCK_ID)).one()[
                0
            ]
        )
        assert acquired, "another session is holding the cycle lock"

        report = cycle(session)
        assert report.sent == 0
        assert any("already running" in e["error"] for e in report.errors)
    finally:
        holder.exec(text("SELECT pg_advisory_unlock_all()")).one()
        holder.close()


# ===========================================================================
# Audit trail. Doc §3 Stage 6.
# ===========================================================================


def test_every_decision_is_audited(session, merchant, customer):
    make_invoice(session, merchant, customer, overdue=TIER_1_DAYS_OVERDUE)
    cycle(session)

    actions = {a.action for a in session.exec(select(AuditLog)).all()}
    assert AuditAction.DIAGNOSED in actions
    assert AuditAction.POLICY_EVALUATED in actions
    assert AuditAction.REMINDER_SENT in actions


def test_the_policy_decision_is_stored_with_the_reminder(session, merchant, customer):
    """The decision and the message it approved must never drift apart."""
    make_invoice(session, merchant, customer, overdue=TIER_1_DAYS_OVERDUE)
    cycle(session)

    reminder = session.exec(select(Reminder)).one()
    assert reminder.policy_decision["approved"] is True
    assert len(reminder.policy_decision["checks"]) == 10
    assert "Result: APPROVED" in reminder.policy_decision["rendered"]


def test_reminders_record_how_they_were_written(session, merchant, customer):
    make_invoice(session, merchant, customer, overdue=TIER_1_DAYS_OVERDUE)
    cycle(session)
    reminder = session.exec(select(Reminder)).one()
    assert reminder.generated_by == "template_fallback"
    assert reminder.provider == "dry_run"
    assert reminder.sent_at is not None


# ===========================================================================
# A failed delivery must not advance the cadence. Regression, 2026-09-01.
#
# A live workspace with no verified sending domain had every send refused by
# `assert_can_send`. The cycle ignored the outcome: it counted the attempt as
# sent and, at Tier 3, escalated the invoice as "tier_3_reached" — which the
# dashboard renders as "all three automated reminders have been sent". The
# customer had received nothing, and HUMAN_REVIEW meant it was never retried.
# ===========================================================================


@pytest.fixture
def live_merchant(session, merchant):
    """The gate only applies to live workspaces; the default fixture is a demo one."""
    merchant.is_demo = False
    merchant.mode = "live"
    session.add(merchant)
    session.commit()
    session.refresh(merchant)
    return merchant


@pytest.fixture
def refuse_sending(monkeypatch):
    """Refuse delivery the way an unverified sending domain does: permanently."""
    from app.services import messaging as messaging_module
    from app.services.outbound_controls import OutboundBlockedError

    def refuse(*_args, **_kwargs):
        raise OutboundBlockedError("No verified sending domain.")

    monkeypatch.setattr(messaging_module, "assert_can_send", refuse)


def test_a_refused_send_is_not_counted_as_sent(session, live_merchant, customer, refuse_sending):
    make_invoice(session, live_merchant, customer, overdue=TIER_1_DAYS_OVERDUE)

    report = cycle(session)

    assert report.sent == 0, "a reminder that never left must not be reported as sent"
    assert report.send_failed == 1
    reminder = session.exec(select(Reminder)).one()
    assert reminder.sent_at is None
    assert reminder.delivery_state == "dead"


def test_a_refused_tier_3_send_does_not_escalate_as_if_it_had_been_sent(
    session, live_merchant, customer, refuse_sending
):
    invoice = make_invoice(session, live_merchant, customer, overdue=TIER_3_DAYS_OVERDUE)

    report = cycle(session)

    session.refresh(invoice)
    assert invoice.escalation_reason != "tier_3_reached"
    assert invoice.status != InvoiceStatus.HUMAN_REVIEW, (
        "parking the invoice for a human implies three reminders went out; none did"
    )
    assert report.escalated == 0
    assert invoice.reminders_sent == 0


def test_the_tier_is_still_owed_after_a_refused_send(session, live_merchant, customer, monkeypatch):
    """Once sending works again the cadence must resume, not skip the missed tier."""
    from app.services import messaging as messaging_module
    from app.services.outbound_controls import OutboundBlockedError

    def refuse(*_args, **_kwargs):
        raise OutboundBlockedError("No verified sending domain.")

    monkeypatch.setattr(messaging_module, "assert_can_send", refuse)
    invoice = make_invoice(session, live_merchant, customer, overdue=TIER_1_DAYS_OVERDUE)
    cycle(session)
    session.refresh(invoice)
    assert invoice.reminders_sent == 0

    # Sending is fixed; the same tier must go out rather than being consumed.
    monkeypatch.setattr(messaging_module, "assert_can_send", lambda *a, **k: None)
    report = cycle(session)

    session.refresh(invoice)
    assert report.sent == 1
    assert invoice.reminders_sent == 1
    assert {r.tier for r in session.exec(select(Reminder)).all() if r.sent_at} == {1}


def test_a_dry_run_never_redelivers_a_dead_reminder(
    session, live_merchant, customer, refuse_sending, monkeypatch
):
    """Reviving a dead reminder delivers, and that branch sits above the dry-run guard.

    Patched on `recovery` rather than `messaging`: the cycle imported the name directly,
    so patching the defining module would not be seen by the code under test.
    """
    make_invoice(session, live_merchant, customer, overdue=TIER_1_DAYS_OVERDUE)
    cycle(session)

    calls: list[object] = []
    monkeypatch.setattr(recovery_module, "redeliver_reminder", lambda *a, **k: calls.append(a))
    report = cycle(session, dry_run=True)

    assert calls == [], "a dry run must not deliver anything"
    assert report.deliveries_revived == 1
    assert session.exec(select(Reminder)).one().sent_at is None
