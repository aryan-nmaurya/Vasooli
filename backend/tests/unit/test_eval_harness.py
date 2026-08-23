"""The evaluation harness itself. Doc §9.

An eval nobody checks is a number generator. These tests cover the parts where a bug
would produce a plausible-looking but wrong result — which is worse than an obviously
broken one, because it gets believed.
"""

from collections import Counter

import pytest

from app.core.constants import MAX_AUTOMATED_REMINDERS
from eval.config import BEHAVIOURS, SIMULATION_DAYS
from eval.metrics import EvalResult, PolicyViolations


def result(**kw) -> EvalResult:
    base = dict(
        policy="vasooli",
        invoices=100,
        total_overdue_paise=0,
        recovered_paise=0,
        recovery_rate=0.0,
        avg_days_to_recovery=None,
        automation_rate=None,
        total_contacts=0,
        contacts_per_invoice=0.0,
        escalated=0,
        false_escalations=0,
        missed_escalations=0,
        promises_logged=0,
        promises_kept=0,
        promises_broken=0,
        diagnosis_correct=0,
        diagnosis_total=0,
        diagnosis_reclassified=0,
        confusion=Counter(),
        policy_rejections=Counter(),
        violations=PolicyViolations(),
    )
    base.update(kw)
    return EvalResult(**base)


# ===========================================================================
# The behaviour model. Fixed before results were looked at.
# ===========================================================================


def test_every_generated_outcome_has_a_behaviour():
    """A missing entry would silently drop invoices from the simulation."""
    from scripts.generate_synthetic import PROFILES

    for profile in PROFILES:
        assert profile.outcome in BEHAVIOURS


def test_a_defaulter_never_pays():
    """Otherwise the recovery rate measures the simulator's generosity, not the policy."""
    assert BEHAVIOURS["would_default"].pays_after_tier is None
    assert BEHAVIOURS["would_default"].promise_kept_prob == 0.0


def test_someone_who_would_pay_anyway_needs_no_reminder():
    assert BEHAVIOURS["would_pay_anyway"].pays_after_tier == 0


def test_behaviour_probabilities_are_probabilities():
    for name, b in BEHAVIOURS.items():
        assert 0 <= b.reply_prob <= 1, name
        assert 0 <= b.promise_prob <= 1, name
        assert 0 <= b.promise_kept_prob <= 1, name


def test_the_window_is_long_enough_to_finish_the_cadence():
    """A window shorter than Tier 3 would score the policy on a cadence it never ran."""
    from app.core.constants import TIER_3_DAYS_OVERDUE

    assert SIMULATION_DAYS > TIER_3_DAYS_OVERDUE


# ===========================================================================
# Diagnosis accounting.
# ===========================================================================


def test_reclassification_is_not_counted_as_a_mistake():
    """Doc §3 defines unresponsive as no reply after Tier 2, so a customer who stops
    answering genuinely changes category. Scoring that as an error would understate
    accuracy by treating a correct rule as a failure."""
    r = result(diagnosis_total=100, diagnosis_correct=85, diagnosis_reclassified=13)
    assert r.diagnosis_accuracy == pytest.approx(0.85)
    assert r.diagnosis_defensible == pytest.approx(0.98)


def test_accuracy_on_an_empty_run_does_not_divide_by_zero():
    assert result().diagnosis_accuracy == 0.0
    assert result().diagnosis_defensible == 0.0


# ===========================================================================
# Violations are failures, not report lines.
# ===========================================================================


def test_violations_total_counts_every_category():
    v = PolicyViolations(
        over_cap=["a"],
        disputed_contacted=["b", "c"],
        cooldown_breached=["d"],
        contacted_after_payment=["e"],
    )
    assert v.total == 5


def test_a_clean_run_reports_zero():
    assert PolicyViolations().total == 0


def test_the_naive_baseline_would_breach_the_cap():
    """Guards the comparison's honesty.

    The naive policy contacts customers roughly every three days for 45 days. If the
    reported breach count for it were ever zero, the harness would be hiding the very
    thing the comparison exists to show.
    """
    contacts_per_invoice = (SIMULATION_DAYS - 3) // 3
    assert contacts_per_invoice > MAX_AUTOMATED_REMINDERS
