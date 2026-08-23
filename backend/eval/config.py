"""Simulation parameters. Doc §9.

These numbers are fixed BEFORE any results are looked at. A simulator tuned until the
recovery rate looks impressive measures nothing except the tuning, and a panel that
suspects that discounts every other number too.

The behaviour model is deliberately simple and stated in the open: each synthetic
customer was generated with a known outcome, and that outcome decides how they react.
Vasooli's policy does not get to change whether a customer *can* pay — only how quickly
they are prompted to, and whether they are chased after they already have.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Behaviour:
    #: Reminders they need before paying. 0 = pays unprompted. None = never pays.
    pays_after_tier: int | None
    #: Days between that trigger and the money landing.
    payment_delay_days: tuple[int, int]
    #: Chance of replying at all to a reminder.
    reply_prob: float
    #: Chance a reply contains a commitment to pay.
    promise_prob: float
    #: Chance a promise made is actually honoured.
    promise_kept_prob: float


BEHAVIOURS: dict[str, Behaviour] = {
    # Would have paid without any chasing. Chasing these is pure cost, and escalating
    # one is a false positive the eval counts against us.
    "would_pay_anyway": Behaviour(
        pays_after_tier=0,
        payment_delay_days=(1, 8),
        reply_prob=0.10,
        promise_prob=0.0,
        promise_kept_prob=1.0,
    ),
    # A single polite reminder is enough.
    "needs_one_nudge": Behaviour(
        pays_after_tier=1,
        payment_delay_days=(1, 5),
        reply_prob=0.45,
        promise_prob=0.30,
        promise_kept_prob=0.85,
    ),
    # Needs to be asked more than once; often promises first, and keeps it about half
    # the time. This is the group the promise loop exists for.
    "needs_multiple": Behaviour(
        pays_after_tier=2,
        payment_delay_days=(2, 9),
        reply_prob=0.70,
        promise_prob=0.60,
        promise_kept_prob=0.50,
    ),
    # Will not pay inside the window whatever we do. The measure here is whether we
    # stop chasing and hand over rather than nagging indefinitely.
    "would_default": Behaviour(
        pays_after_tier=None,
        payment_delay_days=(0, 0),
        reply_prob=0.12,
        promise_prob=0.25,
        promise_kept_prob=0.0,
    ),
}

#: How long a merchant would reasonably wait before writing an invoice off.
SIMULATION_DAYS = 45

#: Naive baseline: contact every N days, no caps, no policy, no promise handling.
NAIVE_INTERVAL_DAYS = 3

PROMISE_HORIZON_DAYS = (5, 12)

REPLY_TEMPLATES = {
    "promise": [
        "Sorry for the delay — I'll clear this by {date}.",
        "Payment is scheduled. You'll have it by {date}.",
        "Cash is tight this cycle. Can pay by {date} without fail.",
    ],
    "complaint": [
        "We were billed for 12 units but received 9. Please check before we pay.",
        "This invoice doesn't match the PO we signed. Holding payment for now.",
        "There's a pricing mismatch versus what was agreed.",
    ],
    "vague": [
        "Thanks, noted.",
        "I'll look into it.",
        "Received, will check with accounts.",
    ],
}
