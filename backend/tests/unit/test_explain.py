"""Plain-language explanations. P1.

Deterministic on purpose. The sentence must match what the policy engine actually did
— a generated explanation that drifts from the decision is worse than none, because it
teaches a merchant to distrust the whole screen.
"""

from datetime import date

import pytest

from app.core.constants import (
    MAX_AUTOMATED_REMINDERS,
    MIN_COOLDOWN_DAYS,
    InvoiceStatus,
    ReasonCategory,
)
from app.services.explain import explain


def why(**kw):
    base = dict(
        status=InvoiceStatus.CHASING,
        days_overdue=12,
        reminders_sent=1,
        current_tier=1,
        reason_category=ReasonCategory.CASH_CONSTRAINED,
        escalation_reason=None,
        amount_paise=5_000_000,
        amount_paid_paise=0,
        active_promise_date=None,
        days_since_last_reminder=None,
        has_failed_delivery=False,
    )
    base.update(kw)
    return explain(**base)


# ===========================================================================
# The four examples from the brief.
# ===========================================================================


def test_a_due_reminder_says_why():
    result = why(days_overdue=12, reminders_sent=1)
    assert "Tier 2" in result.headline
    assert "12 days overdue" in result.headline
    assert result.why_state if False else result.state == "active"


def test_a_promise_explains_the_pause():
    result = why(active_promise_date=date(2026, 9, 4))
    assert "paused" in result.headline.lower()
    assert "04 September" in result.headline
    assert result.state == "paused"


def test_a_confirmed_payment_explains_the_stop():
    result = why(
        status=InvoiceStatus.RECOVERED, amount_paise=5_000_000, amount_paid_paise=5_000_000
    )
    assert "stopped" in result.headline.lower()
    assert "₹50,000" in result.headline
    assert result.state == "stopped"


def test_a_dispute_explains_the_escalation():
    result = why(status=InvoiceStatus.HUMAN_REVIEW, escalation_reason="dispute_likely")
    assert "disputes this invoice" in result.headline
    assert "will not contact" in result.next_step
    assert result.state == "stopped"


# ===========================================================================
# The rules a merchant would otherwise have to infer.
# ===========================================================================


def test_not_yet_due_names_the_threshold():
    result = why(days_overdue=2, reminders_sent=0)
    assert "not yet due" in result.headline.lower()
    assert "3 days overdue" in result.next_step
    assert result.state == "waiting"


@pytest.mark.parametrize(
    ("days_since", "expected"),
    [(0, "today"), (1, "yesterday"), (3, "3 days ago")],
)
def test_a_cooldown_hold_reads_naturally(days_since, expected):
    """ "0 days ago" is technically true and reads like a bug."""
    result = why(days_overdue=12, reminders_sent=1, days_since_last_reminder=days_since)
    assert expected in result.headline
    assert str(MIN_COOLDOWN_DAYS) in result.next_step
    assert result.state == "paused"


def test_the_cap_is_explained_not_just_applied():
    result = why(reminders_sent=MAX_AUTOMATED_REMINDERS, days_overdue=40)
    assert str(MAX_AUTOMATED_REMINDERS) in result.headline
    assert result.state == "stopped"


def test_tier_3_handover_is_explained():
    result = why(status=InvoiceStatus.HUMAN_REVIEW, escalation_reason="tier_3_reached")
    assert "all three automated reminders" in result.headline.lower()


def test_a_partial_payment_shows_both_figures():
    result = why(
        status=InvoiceStatus.PARTIALLY_PAID, amount_paise=5_000_000, amount_paid_paise=2_000_000
    )
    assert "₹20,000" in result.headline
    assert "₹30,000" in result.headline
    assert result.state == "active"


def test_a_failed_delivery_says_the_tier_was_not_consumed():
    """The reassurance that matters: a bounce did not silently use up a reminder."""
    result = why(has_failed_delivery=True, days_overdue=12)
    assert "could not be delivered" in result.headline
    assert "NOT been counted" in result.next_step


def test_a_write_off_stops_everything():
    assert why(status=InvoiceStatus.WRITTEN_OFF).state == "stopped"


# ===========================================================================
# Money is always rendered in Indian format.
# ===========================================================================


@pytest.mark.parametrize(
    ("paise", "expected"),
    [(5_000_000, "₹50,000"), (64_000_000, "₹6,40,000"), (100_000, "₹1,000")],
)
def test_amounts_use_indian_grouping(paise, expected):
    result = why(status=InvoiceStatus.RECOVERED, amount_paise=paise, amount_paid_paise=paise)
    assert expected in result.headline


# ===========================================================================
# A dispute is paused, not finished. Customer Conversation Safety.
# ===========================================================================


def _human_review(reason: str):
    return explain(
        status=InvoiceStatus.HUMAN_REVIEW,
        days_overdue=12,
        reminders_sent=2,
        current_tier=2,
        reason_category=None,
        escalation_reason=reason,
        amount_paise=2_500_000,
        amount_paid_paise=0,
        active_promise_date=None,
        days_since_last_reminder=4,
    )


def test_a_dispute_reads_as_paused_not_stopped():
    """The card must not contradict the resume button underneath it."""
    result = _human_review("complaint_in_reply")
    assert result.state == "paused"
    assert "disputes this invoice" in result.headline
    assert "will not contact" not in result.next_step


def test_a_dispute_says_no_reminder_goes_out_while_it_is_open():
    assert "No automated reminder" in _human_review("complaint_in_reply").next_step


def test_every_other_escalation_still_reads_as_stopped():
    """Tier 3 and a manual escalation are one-way doors; a dispute is not."""
    for reason in ("tier_3_reached", "manual", "dispute_likely"):
        assert _human_review(reason).state == "stopped"
