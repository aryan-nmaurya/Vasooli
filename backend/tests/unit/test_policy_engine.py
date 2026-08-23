"""The policy engine. Doc §5.

This is the largest test file in the project on purpose. Every rule here encodes a
compliance promise — never more than three contacts, never inside a week, never a
threat, never an automated chase on a disputed invoice — and a promise that is only
tested on the happy path is not a promise.

The engine is pure, so these are plain table-driven tests: no database, no fixtures,
no clock.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.constants import (
    MAX_AUTOMATED_REMINDERS,
    MIN_COOLDOWN_DAYS,
    PROMISE_GRACE_DAYS,
    TIER_SCHEDULE,
    InvoiceStatus,
    ReasonCategory,
    Tone,
)
from app.policy import RequiredAction, evaluate_reminder, next_tier_for, tone_for_tier

NOW = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)

CLEAN_SUBJECT = "Invoice INV-2291 — payment reminder"
CLEAN_BODY = "Hello, invoice INV-2291 for ₹42,000 was due on 1 August. Payment link: ..."


def decide(**overrides):
    """An invoice that passes every check, unless a test changes something."""
    kwargs = dict(
        invoice_number="INV-2291",
        status=InvoiceStatus.CHASING,
        reason_category=ReasonCategory.OVERSIGHT,
        has_prior_dispute_note=False,
        outstanding_paise=4_200_000,
        days_overdue=3,
        reminders_sent=0,
        sent_tiers=frozenset(),
        last_reminder_at=None,
        active_promise_date=None,
        proposed_tier=1,
        drafted_subject=CLEAN_SUBJECT,
        drafted_body=CLEAN_BODY,
        now=NOW,
    )
    kwargs.update(overrides)
    return evaluate_reminder(**kwargs)


def failed_names(decision) -> set[str]:
    return {c.name for c in decision.checks if not c.passed}


# ===========================================================================
# The happy path.
# ===========================================================================


def test_a_clean_tier_1_reminder_is_approved():
    d = decide()
    assert d.approved is True
    assert d.required_action is RequiredAction.SEND
    assert failed_names(d) == set()


def test_every_check_runs_even_when_all_pass():
    """The audit log shows the full evaluation, not just objections."""
    assert len(decide().checks) == 9


def test_every_check_runs_even_when_one_fails():
    """No short-circuiting: a cooldown failure must not hide a banned phrase."""
    d = decide(
        last_reminder_at=NOW - timedelta(days=1),
        drafted_body="We will take legal action.",
    )
    assert len(d.checks) == 9
    assert failed_names(d) == {"cooldown_respected", "no_banned_language"}


# ===========================================================================
# Cadence. Doc §3 Stage 3 — the 3 / 10 / 21 schedule.
# ===========================================================================


@pytest.mark.parametrize(
    ("tier", "days", "expected"),
    [
        (1, 2, False),
        (1, 3, True),
        (1, 4, True),
        (2, 9, False),
        (2, 10, True),
        (2, 11, True),
        (3, 20, False),
        (3, 21, True),
        (3, 30, True),
    ],
)
def test_cadence_thresholds(tier, days, expected):
    d = decide(
        proposed_tier=tier,
        days_overdue=days,
        sent_tiers=frozenset(range(1, tier)),
        reminders_sent=tier - 1,
        last_reminder_at=None,
    )
    assert ("cadence_due" not in failed_names(d)) is expected


def test_tier_thresholds_come_from_the_locked_constants():
    assert TIER_SCHEDULE == {1: 3, 2: 10, 3: 21}


def test_invalid_tier_is_rejected():
    d = decide(proposed_tier=4, days_overdue=100)
    assert d.approved is False
    assert "cadence_due" in failed_names(d)


# ===========================================================================
# The reminder cap. The single hardest promise in the spec.
# ===========================================================================


@pytest.mark.parametrize("sent", [0, 1, 2])
def test_under_the_cap_is_allowed(sent):
    d = decide(
        reminders_sent=sent,
        proposed_tier=sent + 1,
        days_overdue=TIER_SCHEDULE[sent + 1],
        sent_tiers=frozenset(range(1, sent + 1)),
    )
    assert "reminder_cap" not in failed_names(d)


def test_at_the_cap_escalates_to_a_human():
    """Escalates rather than holds — a held invoice would sit in the queue forever."""
    d = decide(reminders_sent=MAX_AUTOMATED_REMINDERS, proposed_tier=3, days_overdue=40)
    assert d.approved is False
    assert d.required_action is RequiredAction.ESCALATE_TO_HUMAN


def test_cap_cannot_be_exceeded_from_any_state():
    """Property check: no combination of inputs approves a fourth reminder."""
    for sent in range(MAX_AUTOMATED_REMINDERS, MAX_AUTOMATED_REMINDERS + 4):
        for tier in (1, 2, 3):
            for days in (3, 10, 21, 60, 365):
                d = decide(
                    reminders_sent=sent,
                    proposed_tier=tier,
                    days_overdue=days,
                    sent_tiers=frozenset(),
                )
                assert d.approved is False, f"approved with {sent} already sent"


# ===========================================================================
# Cooldown. "No same-week repeated contact."
# ===========================================================================


@pytest.mark.parametrize(
    ("days_since", "expected"),
    [(0, False), (1, False), (6, False), (7, True), (8, True), (30, True)],
)
def test_cooldown_boundary(days_since, expected):
    d = decide(
        proposed_tier=2,
        days_overdue=10,
        reminders_sent=1,
        sent_tiers=frozenset({1}),
        last_reminder_at=NOW - timedelta(days=days_since),
    )
    assert ("cooldown_respected" not in failed_names(d)) is expected


def test_cooldown_is_at_least_a_week():
    assert MIN_COOLDOWN_DAYS >= 7


def test_first_contact_has_no_cooldown():
    assert "cooldown_respected" not in failed_names(decide(last_reminder_at=None))


def test_cooldown_wins_over_a_due_tier():
    """A compliance rule outranks the schedule: the tier slips, the customer is not
    contacted twice in a week."""
    d = decide(
        proposed_tier=2,
        days_overdue=10,
        reminders_sent=1,
        sent_tiers=frozenset({1}),
        last_reminder_at=NOW - timedelta(days=4),
    )
    assert d.approved is False
    assert d.required_action is RequiredAction.HOLD


# ===========================================================================
# Dispute routing. Doc §3 Stage 2 — never automated, under any circumstances.
# ===========================================================================


def test_dispute_likely_escalates_instead_of_sending():
    d = decide(reason_category=ReasonCategory.DISPUTE_LIKELY)
    assert d.approved is False
    assert d.required_action is RequiredAction.ESCALATE_TO_HUMAN


def test_prior_dispute_note_also_escalates():
    d = decide(has_prior_dispute_note=True)
    assert d.required_action is RequiredAction.ESCALATE_TO_HUMAN


@pytest.mark.parametrize("tier", [1, 2, 3])
@pytest.mark.parametrize("days", [3, 10, 21, 90])
def test_dispute_never_sends_at_any_tier_or_age(tier, days):
    """The strongest statement the policy makes. It must hold everywhere."""
    d = decide(
        reason_category=ReasonCategory.DISPUTE_LIKELY,
        proposed_tier=tier,
        days_overdue=days,
        reminders_sent=0,
        sent_tiers=frozenset(),
    )
    assert d.required_action is not RequiredAction.SEND


# ===========================================================================
# Banned language. Doc §5 — a rules layer independent of what the model drafts.
# ===========================================================================


@pytest.mark.parametrize(
    ("body", "phrase"),
    [
        ("We will take legal action.", "legal action"),
        ("Our lawyer will be in touch.", "lawyer"),
        ("This is your final warning.", "final warning"),
        ("We will report you to CIBIL.", "CIBIL"),
        ("Pay or face consequences.", "consequences"),
        ("You will be marked a defaulter.", "defaulter"),
        ("We are sending a recovery agent.", "recovery agent"),
    ],
)
def test_threatening_drafts_are_rejected(body, phrase):
    d = decide(drafted_body=body)
    assert d.approved is False
    assert "no_banned_language" in failed_names(d)
    assert phrase in d.reason or any(phrase in c.detail for c in d.checks)


def test_the_matched_phrase_is_named_for_the_regeneration_prompt():
    d = decide(drafted_body="We will take legal action immediately.")
    check = next(c for c in d.checks if c.name == "no_banned_language")
    assert "legal action" in check.detail


@pytest.mark.parametrize(
    "body",
    [
        "l e g a l  a c t i o n will follow",
        "l-e-g-a-l action will follow",
        "LEGAL ACTION",
        "Legal   Action",
    ],
)
def test_evasions_do_not_slip_through(body):
    """Spacing, punctuation, and casing all normalize to the same phrase."""
    assert "no_banned_language" in failed_names(decide(drafted_body=body))


def test_the_subject_line_is_checked_too():
    assert "no_banned_language" in failed_names(decide(drafted_subject="FINAL WARNING"))


@pytest.mark.parametrize(
    "body",
    [
        "Could you confirm a pay-by date?",
        "The invoice remains unpaid. Please arrange payment at your earliest convenience.",
        "We have not received payment for invoice INV-2291. Kindly confirm when we can expect it.",
    ],
)
def test_firm_but_compliant_copy_passes(body):
    """Tier 2 has to be firm. Firm is allowed; threatening is not."""
    assert "no_banned_language" not in failed_names(decide(drafted_body=body))


# ===========================================================================
# Promise pausing. Doc §3 Stage 4.
# ===========================================================================


def test_an_active_promise_pauses_escalation():
    d = decide(
        active_promise_date=date(2026, 8, 28),
        proposed_tier=2,
        days_overdue=10,
        reminders_sent=1,
        sent_tiers=frozenset({1}),
    )
    assert d.approved is False
    assert d.required_action is RequiredAction.HOLD
    assert "no_active_promise" in failed_names(d)


@pytest.mark.parametrize(
    ("promised", "expected_blocked"),
    [
        (date(2026, 8, 25), True),  # future
        (date(2026, 8, 22), True),  # today
        (date(2026, 8, 21), True),  # 1 day past, inside grace
        (date(2026, 8, 20), True),  # exactly at the grace boundary
        (date(2026, 8, 19), False),  # grace expired
        (date(2026, 8, 1), False),  # long expired
    ],
)
def test_grace_window_boundary(promised, expected_blocked):
    """`now` is 2026-08-22, grace is 2 days."""
    d = decide(
        active_promise_date=promised,
        proposed_tier=2,
        days_overdue=10,
        reminders_sent=1,
        sent_tiers=frozenset({1}),
    )
    assert ("no_active_promise" in failed_names(d)) is expected_blocked


def test_grace_period_is_short_but_real():
    assert 1 <= PROMISE_GRACE_DAYS <= 5


def test_a_broken_promise_no_longer_blocks():
    d = decide(
        active_promise_date=date(2026, 8, 10),
        proposed_tier=2,
        days_overdue=10,
        reminders_sent=1,
        sent_tiers=frozenset({1}),
    )
    assert d.approved is True


# ===========================================================================
# Already-resolved invoices.
# ===========================================================================


@pytest.mark.parametrize(
    "status",
    [InvoiceStatus.RECOVERED, InvoiceStatus.WRITTEN_OFF, InvoiceStatus.HUMAN_REVIEW],
)
def test_closed_invoices_are_never_chased(status):
    assert decide(status=status).approved is False


def test_a_zero_balance_is_never_chased():
    """Chasing someone who has already paid is the worst false positive here."""
    d = decide(outstanding_paise=0)
    assert d.approved is False
    assert "amount_still_outstanding" in failed_names(d)


def test_partially_paid_invoices_are_still_chased():
    d = decide(status=InvoiceStatus.PARTIALLY_PAID, outstanding_paise=2_200_000)
    assert d.approved is True


# ===========================================================================
# Repeated tiers.
# ===========================================================================


def test_a_tier_already_sent_is_not_resent():
    d = decide(proposed_tier=1, sent_tiers=frozenset({1}), reminders_sent=1)
    assert d.approved is False
    assert "tier_not_repeated" in failed_names(d)


# ===========================================================================
# Severity ordering.
# ===========================================================================


def test_escalation_outranks_hold_when_both_fail():
    """Past the cap AND inside a cooldown belongs with a human, not back in the queue."""
    d = decide(
        reminders_sent=3, last_reminder_at=NOW - timedelta(days=1), proposed_tier=3, days_overdue=40
    )
    assert d.required_action is RequiredAction.ESCALATE_TO_HUMAN


# ===========================================================================
# Tier / tone mapping and scheduling helpers.
# ===========================================================================


@pytest.mark.parametrize(("tier", "tone"), [(1, Tone.POLITE), (2, Tone.FIRM), (3, Tone.FINAL)])
def test_tone_is_a_policy_decision_not_a_model_choice(tier, tone):
    assert tone_for_tier(tier) == tone.value


@pytest.mark.parametrize(
    ("days", "sent", "expected"),
    [
        (2, frozenset(), None),
        (3, frozenset(), 1),
        (9, frozenset({1}), None),
        (10, frozenset({1}), 2),
        (21, frozenset({1, 2}), 3),
        (21, frozenset({1, 2, 3}), None),
        (25, frozenset(), 3),  # ingested late: goes straight to Tier 3
    ],
)
def test_next_tier_selection(days, sent, expected):
    assert next_tier_for(days_overdue=days, sent_tiers=sent) == expected


# ===========================================================================
# The rendered decision. Doc §5 — this is what the dashboard shows.
# ===========================================================================


def test_rendered_decision_matches_the_spec_shape():
    text = decide().render()
    assert "Invoice: INV-2291" in text
    assert "Proposed action: Send Tier-1 reminder" in text
    assert "Result: APPROVED" in text
    assert text.count("✓") == 9


def test_a_rejection_shows_which_check_failed():
    text = decide(drafted_body="We will take legal action.").render()
    assert "✗" in text
    assert "legal action" in text
    assert "APPROVED" not in text


def test_decision_serializes_for_the_audit_log():
    d = decide()
    payload = d.to_dict()
    assert payload["approved"] is True
    assert len(payload["checks"]) == 9
    assert "rendered" in payload


# ===========================================================================
# Purity. The property that makes the eval harness possible.
# ===========================================================================


def test_the_same_inputs_always_give_the_same_answer():
    assert decide().to_dict() == decide().to_dict()


def test_the_engine_never_reads_the_wall_clock():
    """`now` is injected, so a decision made 'in 2019' is evaluated as 2019."""
    past = decide(
        now=datetime(2019, 1, 1, tzinfo=UTC), last_reminder_at=datetime(2018, 12, 31, tzinfo=UTC)
    )
    assert "cooldown_respected" in failed_names(past)
