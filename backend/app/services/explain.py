"""Plain-language explanation of what Vasooli is doing to an invoice, and why. P1.

A merchant looking at a row should not have to read a timeline to answer "why is this
happening?". The audit trail records what happened; this says what is happening now,
in one sentence.

Deliberately deterministic — no model involved. The explanation must match what the
policy engine actually did, and a generated sentence that drifts from the decision is
worse than no sentence at all.
"""

from dataclasses import dataclass
from datetime import date

from app.core.constants import (
    MAX_AUTOMATED_REMINDERS,
    MIN_COOLDOWN_DAYS,
    TIER_SCHEDULE,
    InvoiceStatus,
    ReasonCategory,
)
from app.core.money import format_inr


@dataclass(frozen=True)
class Explanation:
    #: One sentence, for the row and the header card.
    headline: str
    #: What happens next, if anything.
    next_step: str
    #: "active" | "paused" | "stopped" | "waiting" — drives the badge colour.
    state: str


def explain(
    *,
    status: str,
    days_overdue: int,
    reminders_sent: int,
    current_tier: int,
    reason_category: str | None,
    escalation_reason: str | None,
    amount_paise: int,
    amount_paid_paise: int,
    active_promise_date: date | None,
    days_since_last_reminder: int | None,
    has_failed_delivery: bool = False,
) -> Explanation:
    """Why this invoice is in the state it is in."""
    # --- Stopped -------------------------------------------------------------
    if status == InvoiceStatus.RECOVERED:
        return Explanation(
            headline=f"Recovery stopped — {format_inr(amount_paid_paise)} confirmed received.",
            next_step="Nothing further. The payment link has been closed.",
            state="stopped",
        )

    if status == InvoiceStatus.WRITTEN_OFF:
        return Explanation(
            headline="Written off. No further contact.",
            next_step="Nothing further.",
            state="stopped",
        )

    if status == InvoiceStatus.HUMAN_REVIEW:
        why = {
            "dispute_likely": "the customer disputes this invoice",
            "complaint_in_reply": "the customer raised a complaint in their reply",
            "tier_3_reached": "all three automated reminders have been sent",
            "manual": "someone escalated it by hand",
        }.get(escalation_reason or "", escalation_reason or "it was escalated")
        return Explanation(
            headline=f"Handed to a human because {why}.",
            next_step="Vasooli will not contact this customer again automatically.",
            state="stopped",
        )

    # --- Paused --------------------------------------------------------------
    if active_promise_date is not None:
        return Explanation(
            headline=f"Automation paused — the customer promised to pay by "
            f"{active_promise_date:%d %B}.",
            next_step=f"If nothing arrives, chasing resumes from Tier {current_tier or 1}.",
            state="paused",
        )

    if status == InvoiceStatus.PARTIALLY_PAID:
        outstanding = amount_paise - amount_paid_paise
        return Explanation(
            headline=f"Partly paid — {format_inr(amount_paid_paise)} received, "
            f"{format_inr(outstanding)} still outstanding.",
            next_step="Chasing continues for the balance.",
            state="active",
        )

    if reason_category == ReasonCategory.DISPUTE_LIKELY:
        return Explanation(
            headline="Not being chased — this invoice looks disputed.",
            next_step="A human needs to resolve it.",
            state="stopped",
        )

    # --- Delivery trouble ----------------------------------------------------
    if has_failed_delivery:
        return Explanation(
            headline="A reminder could not be delivered.",
            next_step="Vasooli is retrying. The tier has NOT been counted as sent.",
            state="paused",
        )

    # --- Cap reached ---------------------------------------------------------
    if reminders_sent >= MAX_AUTOMATED_REMINDERS:
        return Explanation(
            headline=f"All {MAX_AUTOMATED_REMINDERS} automated reminders have been sent.",
            next_step="Awaiting handover to a human.",
            state="stopped",
        )

    # --- Waiting on the schedule --------------------------------------------
    next_tier = min(reminders_sent + 1, MAX_AUTOMATED_REMINDERS)
    threshold = TIER_SCHEDULE.get(next_tier, TIER_SCHEDULE[MAX_AUTOMATED_REMINDERS])

    if days_overdue < threshold:
        return Explanation(
            headline=f"{days_overdue} days overdue — not yet due a reminder.",
            next_step=f"Tier {next_tier} is sent at {threshold} days overdue.",
            state="waiting",
        )

    if days_since_last_reminder is not None and days_since_last_reminder < MIN_COOLDOWN_DAYS:
        wait = MIN_COOLDOWN_DAYS - days_since_last_reminder
        return Explanation(
            headline=f"Tier {next_tier} is due, but the last reminder was "
            f"{days_since_last_reminder} days ago.",
            next_step=f"Held for {wait} more day(s) — never two contacts inside "
            f"{MIN_COOLDOWN_DAYS} days.",
            state="paused",
        )

    reason_note = {
        ReasonCategory.OVERSIGHT: "a clean payer who has probably just missed it",
        ReasonCategory.CASH_CONSTRAINED: "a customer who pays late but does pay",
        ReasonCategory.UNRESPONSIVE: "a customer who has not replied",
    }.get(reason_category or "")

    headline = f"Tier {next_tier} reminder due — {days_overdue} days overdue"
    if reason_note:
        headline += f", {reason_note}"
    return Explanation(
        headline=headline + ".",
        next_step=f"Sent on the next cycle, in a {_tone_for(next_tier)} tone.",
        state="active",
    )


def _tone_for(tier: int) -> str:
    return {1: "polite", 2: "firm", 3: "final"}.get(tier, "polite")
