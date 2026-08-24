"""The pure dispute decision. Customer Conversation Safety.

The model's opinion never reaches invoice state directly — it reaches
`decide_dispute_action`, which is a pure function. That is what lets the whole
behaviour be stated here as a table rather than sampled through a database.
"""

import pytest

from app.core.constants import InvoiceStatus
from app.policy import DisputeAction, decide_dispute_action, decide_resume
from app.policy.engine import evaluate_reminder
from app.policy.rules import no_open_dispute

# ===========================================================================
# Detecting a dispute pauses recovery.
# ===========================================================================


def test_a_dispute_on_a_chased_invoice_pauses_recovery():
    decision = decide_dispute_action(
        is_dispute=True, status=InvoiceStatus.CHASING, case_already_open=False
    )
    assert decision.action is DisputeAction.PAUSE_AND_OPEN_CASE
    assert decision.pauses_recovery is True


def test_a_reply_that_is_not_a_dispute_changes_nothing():
    decision = decide_dispute_action(
        is_dispute=False, status=InvoiceStatus.CHASING, case_already_open=False
    )
    assert decision.action is DisputeAction.NO_ACTION
    assert decision.pauses_recovery is False


def test_a_second_dispute_does_not_open_a_second_case():
    """Idempotency, decided rather than caught by an integrity error."""
    decision = decide_dispute_action(
        is_dispute=True, status=InvoiceStatus.CHASING, case_already_open=True
    )
    assert decision.action is DisputeAction.ALREADY_PAUSED
    assert decision.pauses_recovery is False


@pytest.mark.parametrize("status", [InvoiceStatus.RECOVERED, InvoiceStatus.WRITTEN_OFF])
def test_a_dispute_on_a_finished_invoice_pauses_nothing(status):
    decision = decide_dispute_action(is_dispute=True, status=status, case_already_open=False)
    assert decision.action is DisputeAction.NO_RECOVERY_TO_PAUSE


@pytest.mark.parametrize("status", ["recovered", "written_off", "chasing"])
def test_status_is_compared_by_value_not_identity(status):
    """A status loaded from Postgres is a plain str, not the enum.

    The same trap has already produced a live bug on the dispute path in recovery.py,
    so it is pinned here rather than left to be rediscovered.
    """
    decision = decide_dispute_action(is_dispute=True, status=status, case_already_open=False)
    expected = (
        DisputeAction.PAUSE_AND_OPEN_CASE
        if status == "chasing"
        else DisputeAction.NO_RECOVERY_TO_PAUSE
    )
    assert decision.action is expected


def test_confidence_is_not_an_argument_at_all():
    """The pause must not depend on how sure the model was.

    Chasing a customer who IS disputing costs the relationship and cannot be undone;
    pausing on a false positive costs a click. If a `confidence` parameter ever
    appears in this signature, that trade-off has been quietly reversed.
    """
    import inspect

    assert "confidence" not in inspect.signature(decide_dispute_action).parameters


# ===========================================================================
# Resuming.
# ===========================================================================


def test_recovery_cannot_resume_while_the_case_is_open():
    decision = decide_resume(case_is_open=True, status=InvoiceStatus.HUMAN_REVIEW)
    assert decision.action is DisputeAction.ALREADY_PAUSED


def test_recovery_resumes_once_the_case_is_closed():
    decision = decide_resume(case_is_open=False, status=InvoiceStatus.HUMAN_REVIEW)
    assert decision.action is DisputeAction.NO_ACTION


def test_a_paid_invoice_does_not_go_back_into_the_cadence():
    """An operator clicking resume on an invoice that was paid mid-dispute."""
    decision = decide_resume(case_is_open=False, status=InvoiceStatus.RECOVERED)
    assert decision.action is DisputeAction.NO_RECOVERY_TO_PAUSE


# ===========================================================================
# The reminder engine's dispute check.
# ===========================================================================


def _decide(**overrides):
    from datetime import UTC, datetime

    defaults = dict(
        invoice_number="INV-1",
        status=InvoiceStatus.CHASING,
        reason_category=None,
        has_prior_dispute_note=False,
        outstanding_paise=100_000,
        days_overdue=3,
        reminders_sent=0,
        sent_tiers=frozenset(),
        last_reminder_at=None,
        active_promise_date=None,
        proposed_tier=1,
        drafted_subject="Invoice INV-1",
        drafted_body="Please arrange payment.",
        now=datetime(2026, 3, 1, tzinfo=UTC),
    )
    return evaluate_reminder(**{**defaults, **overrides})


def test_an_open_dispute_blocks_the_reminder():
    decision = _decide(has_open_dispute=True)
    assert decision.approved is False
    assert "no_open_dispute" in {c.name for c in decision.failed_checks}


def test_an_open_dispute_escalates_rather_than_holds():
    """A held invoice comes back next cycle. A disputed one must not."""
    from app.policy import RequiredAction

    assert _decide(has_open_dispute=True).required_action is RequiredAction.ESCALATE_TO_HUMAN


def test_the_dispute_check_is_absent_from_a_clean_decision():
    assert _decide().approved is True


def test_the_reason_appears_in_the_rendered_decision():
    """The merchant reads this string in the audit log, so it must say why."""
    rendered = _decide(has_open_dispute=True).render()
    assert "dispute case is open" in rendered
    assert "✗" in rendered


def test_the_rule_is_independent_of_the_diagnosed_category():
    """A customer can dispute an invoice the diagnosis still calls an oversight."""
    check = no_open_dispute(True)
    assert check.passed is False
    assert check.name == "no_open_dispute"
