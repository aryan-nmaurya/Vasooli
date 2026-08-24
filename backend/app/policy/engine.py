"""The policy engine. Doc §5.

Every proposed customer contact passes through `evaluate_reminder` before anything is
sent. The language model produces text; this decides whether that text may leave the
building. Nothing in app.ai can bypass it, because nothing in app.ai can send.

Two design choices are worth stating explicitly:

**All checks always run.** The engine never short-circuits on the first failure. A
decision that stopped at "cooldown not met" would hide that the draft also contained
a banned phrase, and the audit log is supposed to show the complete evaluation, not
the first objection.

**Escalation outranks holding.** When several checks fail, the most severe outcome
wins. An invoice that is both past its reminder cap and inside a cooldown belongs
with a human, not back in the queue.
"""

from datetime import date, datetime

from app.core.constants import TONE_FOR_TIER, InvoiceStatus, ReasonCategory
from app.policy import rules
from app.policy.decisions import PolicyCheck, PolicyDecision, RequiredAction


def evaluate_reminder(
    *,
    invoice_number: str,
    status: InvoiceStatus | str,
    reason_category: ReasonCategory | None,
    has_prior_dispute_note: bool,
    has_open_dispute: bool = False,
    outstanding_paise: int,
    days_overdue: int,
    reminders_sent: int,
    sent_tiers: frozenset[int],
    last_reminder_at: datetime | None,
    active_promise_date: date | None,
    proposed_tier: int,
    drafted_subject: str,
    drafted_body: str,
    now: datetime,
) -> PolicyDecision:
    """Decide whether a drafted reminder may be sent.

    Pure: no database, no network, no wall clock. `days_overdue` is passed in rather
    than derived from `due_at` so this function never needs to know what time it is —
    which is what makes exhaustive table-driven testing and simulated-clock evaluation
    possible.
    """
    checks: list[PolicyCheck] = [
        rules.not_already_resolved(status),
        rules.amount_still_outstanding(outstanding_paise),
        rules.not_dispute_likely(reason_category, has_prior_dispute_note),
        rules.no_open_dispute(has_open_dispute),
        rules.reminder_cap(reminders_sent),
        rules.cadence_due(days_overdue, proposed_tier),
        rules.cooldown_respected(last_reminder_at, now),
        rules.tier_not_repeated(sent_tiers, proposed_tier),
        rules.no_active_promise(active_promise_date, now.date()),
        rules.no_banned_language(drafted_subject, drafted_body),
    ]

    failed = [c for c in checks if not c.passed]

    if not failed:
        return PolicyDecision(
            approved=True,
            required_action=RequiredAction.SEND,
            reason="All policy checks passed",
            checks=checks,
            invoice_number=invoice_number,
            proposed_tier=proposed_tier,
        )

    # Severity order: escalation beats holding.
    escalations = [c for c in failed if c.on_failure is RequiredAction.ESCALATE_TO_HUMAN]
    action = RequiredAction.ESCALATE_TO_HUMAN if escalations else RequiredAction.HOLD
    driving = escalations[0] if escalations else failed[0]

    return PolicyDecision(
        approved=False,
        required_action=action,
        reason=driving.detail,
        checks=checks,
        invoice_number=invoice_number,
        proposed_tier=proposed_tier,
    )


def tone_for_tier(tier: int) -> str:
    """The tone a tier must use. Doc §3 Stage 3.

    Lives here rather than in the AI layer so tone is a policy decision the model is
    told about, not one it makes.
    """
    return TONE_FOR_TIER[tier].value


def next_tier_for(*, days_overdue: int, sent_tiers: frozenset[int]) -> int | None:
    """The highest tier now due that has not been sent yet.

    Returns the highest rather than the lowest so an invoice ingested at day 25 goes
    straight to Tier 3 instead of walking politely up from Tier 1 three weeks late —
    the customer's situation is what the tier should reflect, not our discovery of it.
    """
    from app.core.constants import TIER_SCHEDULE

    due = [t for t, required in TIER_SCHEDULE.items() if days_overdue >= required]
    unsent = [t for t in due if t not in sent_tiers]
    return max(unsent) if unsent else None
