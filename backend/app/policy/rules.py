"""Individual policy rules. Doc §5.

Each rule is a pure function returning a PolicyCheck. Splitting them this way means
every rule is testable on its own, and the engine's only job is to run all of them and
combine the results — there is no branching logic hidden between the checks.

Note what is NOT imported here: no database, no clock, no network. `now` and
`days_overdue` arrive as arguments. That is what lets the Phase 11 eval harness run
thousands of simulated days against this exact code rather than a reimplementation.
"""

from datetime import date, datetime, timedelta

from app.core.constants import (
    MAX_AUTOMATED_REMINDERS,
    MIN_COOLDOWN_DAYS,
    PROMISE_GRACE_DAYS,
    TIER_SCHEDULE,
    InvoiceStatus,
    ReasonCategory,
)
from app.policy.banned_language import find_banned_phrases
from app.policy.decisions import PolicyCheck, RequiredAction


def cadence_due(days_overdue: int, proposed_tier: int) -> PolicyCheck:
    """Has this tier's day count been reached? Doc §3 Stage 3."""
    required = TIER_SCHEDULE.get(proposed_tier)
    if required is None:
        return PolicyCheck(
            name="cadence_due",
            passed=False,
            detail=f"Tier {proposed_tier} is not a valid tier",
            on_failure=RequiredAction.HOLD,
        )
    ok = days_overdue >= required
    return PolicyCheck(
        name="cadence_due",
        passed=ok,
        detail=f"Days overdue ({days_overdue}) {'≥' if ok else '<'} Tier-{proposed_tier} "
        f"threshold ({required})",
        on_failure=RequiredAction.HOLD,
    )


def cooldown_respected(last_reminder_at: datetime | None, now: datetime) -> PolicyCheck:
    """No same-week repeated contact. Doc §3 Stage 3.

    A compliance rule, so it wins over the tier schedule when the two disagree: a
    reminder that slipped a day pushes the next tier later rather than contacting the
    customer twice inside a week.
    """
    if last_reminder_at is None:
        return PolicyCheck(
            name="cooldown_respected",
            passed=True,
            detail="No previous contact",
            on_failure=RequiredAction.HOLD,
        )
    elapsed = (now - last_reminder_at).days
    ok = elapsed >= MIN_COOLDOWN_DAYS
    return PolicyCheck(
        name="cooldown_respected",
        passed=ok,
        detail=f"Days since last contact ({elapsed}) {'≥' if ok else '<'} "
        f"cooldown ({MIN_COOLDOWN_DAYS})",
        on_failure=RequiredAction.HOLD,
    )


def reminder_cap(reminders_sent: int) -> PolicyCheck:
    """Never more than three automated contacts. Doc §3 Stage 3.

    Failing this escalates rather than holds: the invoice has exhausted automation and
    a human must take over. Holding would leave it stuck in the queue forever.
    """
    ok = reminders_sent < MAX_AUTOMATED_REMINDERS
    return PolicyCheck(
        name="reminder_cap",
        passed=ok,
        detail=f"Reminder count ({reminders_sent}) {'<' if ok else '≥'} "
        f"cap ({MAX_AUTOMATED_REMINDERS})",
        on_failure=RequiredAction.ESCALATE_TO_HUMAN,
    )


def tier_not_repeated(sent_tiers: frozenset[int], proposed_tier: int) -> PolicyCheck:
    """Each tier is sent at most once.

    Guards against an overlapping scheduler run or a restart mid-cycle re-sending a
    tier the customer already received.
    """
    ok = proposed_tier not in sent_tiers
    return PolicyCheck(
        name="tier_not_repeated",
        passed=ok,
        detail=f"Tier {proposed_tier} {'not yet sent' if ok else 'already sent'}",
        on_failure=RequiredAction.HOLD,
    )


def no_active_promise(
    promised_date: date | None, today: date, *, grace_days: int = PROMISE_GRACE_DAYS
) -> PolicyCheck:
    """Escalation pauses while a promise is in effect. Doc §3 Stage 4.

    The grace period matters: chasing someone the morning after the date they named
    is the behaviour that makes an automated chaser feel like a nag, and a payment
    initiated on the promised day may take a day or two to land.
    """
    if promised_date is None:
        return PolicyCheck(
            name="no_active_promise",
            passed=True,
            detail="No active promise-to-pay in effect",
            on_failure=RequiredAction.HOLD,
        )
    deadline = promised_date + timedelta(days=grace_days)
    ok = today > deadline
    return PolicyCheck(
        name="no_active_promise",
        passed=ok,
        detail=(
            f"Promise for {promised_date} expired {deadline} (grace {grace_days}d)"
            if ok
            else f"Active promise until {deadline} (promised {promised_date}, grace {grace_days}d)"
        ),
        on_failure=RequiredAction.HOLD,
    )


def not_dispute_likely(reason: ReasonCategory | None, has_prior_dispute: bool) -> PolicyCheck:
    """Disputed invoices never enter the automated cadence. Doc §3 Stage 2.

    An automated nudge is the wrong tool for a customer who believes they were billed
    incorrectly — it escalates a disagreement instead of resolving it. These go
    straight to a human.
    """
    disputed = reason is ReasonCategory.DISPUTE_LIKELY or has_prior_dispute
    return PolicyCheck(
        name="not_dispute_likely",
        passed=not disputed,
        detail="Customer not flagged dispute-likely"
        if not disputed
        else "Customer flagged dispute-likely — automated cadence does not apply",
        on_failure=RequiredAction.ESCALATE_TO_HUMAN,
    )


def no_banned_language(subject: str, body: str) -> PolicyCheck:
    """The drafted message contains no threatening language. Doc §5.

    Runs on the model's output, before sending. Names the phrases it found so the
    regeneration prompt can be specific and the audit log is self-explanatory.
    """
    found = find_banned_phrases(f"{subject}\n{body}")
    return PolicyCheck(
        name="no_banned_language",
        passed=not found,
        detail="No banned phrases in drafted message"
        if not found
        else f"Banned phrase(s) in drafted message: {', '.join(found)}",
        on_failure=RequiredAction.HOLD,
    )


def not_already_resolved(status: InvoiceStatus | str) -> PolicyCheck:
    """Never chase an invoice that is settled, written off, or already with a human."""
    closed = {
        InvoiceStatus.RECOVERED,
        InvoiceStatus.WRITTEN_OFF,
        InvoiceStatus.HUMAN_REVIEW,
    }
    ok = status not in closed
    return PolicyCheck(
        name="not_already_resolved",
        passed=ok,
        detail=f"Invoice status is {status}" + ("" if ok else " — outside the automated queue"),
        on_failure=RequiredAction.HOLD,
    )


def amount_still_outstanding(outstanding_paise: int) -> PolicyCheck:
    """Do not chase a balance of zero.

    Separate from the status check because a payment can settle the balance before the
    status has caught up, and chasing someone who has already paid is the worst false
    positive this system can produce.
    """
    ok = outstanding_paise > 0
    return PolicyCheck(
        name="amount_still_outstanding",
        passed=ok,
        detail=f"Outstanding balance is {outstanding_paise} paise",
        on_failure=RequiredAction.HOLD,
    )
