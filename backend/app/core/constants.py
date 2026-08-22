"""Locked domain constants for Vasooli.

This is the ONLY place the escalation cadence, caps, and enum values are defined.
Every other module imports from here. See Docs/Vasooli_Documentation.md §3 (Stage 2,
Stage 3) — that spec section is the source of truth; this file is its executable form.

Rule enforced in review: a literal 3, 10, or 21 appearing anywhere else in a cadence
context is a bug.
"""

from enum import StrEnum

# --- Escalation cadence: exact day counts past due. Doc §3 Stage 3. ---
TIER_1_DAYS_OVERDUE = 3
TIER_2_DAYS_OVERDUE = 10
TIER_3_DAYS_OVERDUE = 21

TIER_SCHEDULE: dict[int, int] = {
    1: TIER_1_DAYS_OVERDUE,
    2: TIER_2_DAYS_OVERDUE,
    3: TIER_3_DAYS_OVERDUE,
}

# Hard caps enforced by the policy engine (Doc §3 Stage 3, "Hard rules").
MAX_AUTOMATED_REMINDERS = 3  # never fully autonomous beyond this
MIN_COOLDOWN_DAYS = 7  # "no same-week repeated contact"
PROMISE_GRACE_DAYS = 2  # buffer after a promised date before escalation resumes

# Business timezone. Overdue-day math must match what an Indian merchant sees.
BUSINESS_TIMEZONE = "Asia/Kolkata"


class ReasonCategory(StrEnum):
    """The four reason categories. Locked schema — Doc §2 Stage 2.

    Precedence when signals conflict: DISPUTE_LIKELY > UNRESPONSIVE > OVERSIGHT
    > CASH_CONSTRAINED.
    """

    OVERSIGHT = "oversight"
    CASH_CONSTRAINED = "cash_constrained"
    DISPUTE_LIKELY = "dispute_likely"
    UNRESPONSIVE = "unresponsive"


class Tone(StrEnum):
    POLITE = "polite"  # Tier 1
    FIRM = "firm"  # Tier 2
    FINAL = "final"  # Tier 3


TONE_FOR_TIER: dict[int, Tone] = {
    1: Tone.POLITE,
    2: Tone.FIRM,
    3: Tone.FINAL,
}


class InvoiceStatus(StrEnum):
    PENDING = "pending"  # not yet overdue
    CHASING = "chasing"  # in the automated cadence
    PROMISE_ACTIVE = "promise_active"  # escalation paused, promise in effect
    HUMAN_REVIEW = "human_review"  # flagged, out of automation
    PARTIALLY_PAID = "partially_paid"
    RECOVERED = "recovered"
    WRITTEN_OFF = "written_off"


#: Statuses that take an invoice out of the automated cadence permanently.
TERMINAL_STATUSES = frozenset({InvoiceStatus.RECOVERED, InvoiceStatus.WRITTEN_OFF})


class PromiseStatus(StrEnum):
    ACTIVE = "active"
    KEPT = "kept"
    BROKEN = "broken"
