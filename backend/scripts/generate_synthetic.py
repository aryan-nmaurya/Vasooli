"""Generate synthetic receivables ledgers. Phase 2.

    uv run python -m scripts.generate_synthetic

The data is generated FROM the four reason categories, not labelled afterwards. That
ordering matters: the categories are defined as rules over customer history (Doc §3
Stage 2), so a generator that invents plausible-looking customers and then guesses
labels would produce rows the classifier cannot possibly get right, and an eval built
on them would measure noise.

Each profile below states the customer history its category requires, and the
generator only produces histories consistent with the label it is aiming for.

Ground-truth columns are written to the CSV for the Phase 11 eval. They are stripped
at the ingestion boundary (see app/schemas/invoice.py) and never reach the database.
"""

import argparse
import csv
import json
import pathlib
import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.core.constants import (
    TIER_1_DAYS_OVERDUE,
    TIER_2_DAYS_OVERDUE,
    TIER_3_DAYS_OVERDUE,
    ReasonCategory,
)

DATA_DIR = pathlib.Path(__file__).resolve().parents[1] / "data"

#: Razorpay refuses payment links above this amount on this merchant's account
#: ("amount exceeds maximum amount allowed"). Verified empirically: ₹50,000 is
#: accepted, ₹60,000 is not. This is an account limit, not a business rule — a real
#: B2B ledger would carry far larger invoices. Customer averages below are chosen so
#: that even a cash-constrained invoice at 3x the customer's norm stays under it.
MAX_INVOICE_INR = 50_000

Outcome = str  # would_pay_anyway | needs_one_nudge | needs_multiple | would_default


@dataclass(frozen=True)
class Profile:
    label: ReasonCategory
    outcome: Outcome
    weight: float


#: Weighted mix. Deliberately not uniform — a real ledger is mostly oversight and
#: cash-constrained, with a long tail of genuine defaulters.
PROFILES = [
    Profile(ReasonCategory.OVERSIGHT, "needs_one_nudge", 0.30),
    Profile(ReasonCategory.OVERSIGHT, "would_pay_anyway", 0.10),
    Profile(ReasonCategory.CASH_CONSTRAINED, "needs_multiple", 0.25),
    Profile(ReasonCategory.DISPUTE_LIKELY, "needs_multiple", 0.15),
    Profile(ReasonCategory.UNRESPONSIVE, "would_default", 0.20),
]

#: Every tier boundary, and both sides of each. Seeding these explicitly means the
#: policy engine's cadence checks are exercised on day one of the demo rather than
#: whenever random data happens to land on a boundary.
BOUNDARY_DAYS = [
    TIER_1_DAYS_OVERDUE - 1,  # 2  — not yet due a reminder
    TIER_1_DAYS_OVERDUE,  # 3  — Tier 1 fires
    TIER_2_DAYS_OVERDUE - 1,  # 9  — held
    TIER_2_DAYS_OVERDUE,  # 10 — Tier 2 fires
    TIER_3_DAYS_OVERDUE - 1,  # 20 — held
    TIER_3_DAYS_OVERDUE,  # 21 — Tier 3 + human handoff
    30,  # well past the cadence; must stay capped
]

FIRST = [
    "Aarav",
    "Vihaan",
    "Diya",
    "Ananya",
    "Kabir",
    "Meera",
    "Rohan",
    "Ishita",
    "Arjun",
    "Priya",
    "Nikhil",
    "Sanya",
    "Karan",
    "Neha",
    "Rahul",
    "Tara",
]
HOUSES = [
    "Traders",
    "Enterprises",
    "Industries",
    "Textiles",
    "Agencies",
    "Distributors",
    "Exports",
    "Solutions",
    "Retail",
    "Supplies",
    "Packaging",
    "Logistics",
]
PREFIX = [
    "Nova",
    "Kiran",
    "Shakti",
    "Meridian",
    "Ashoka",
    "Sunrise",
    "Deccan",
    "Orbit",
    "Vertex",
    "Sagar",
    "Anand",
    "Prime",
    "Lotus",
    "Vega",
    "Indus",
    "Crest",
]


def _company(rng: random.Random) -> str:
    return f"{rng.choice(PREFIX)} {rng.choice(HOUSES)}"


def _email(company: str, rng: random.Random) -> str:
    slug = company.lower().replace(" ", ".").replace("&", "and")
    return f"{rng.choice(FIRST).lower()}@{slug}.example.com"


def _phone(rng: random.Random) -> str:
    """Indian mobile: 10 digits starting 6-9. Razorpay validates the shape."""
    return f"+91{rng.choice('6789')}{''.join(rng.choice('0123456789') for _ in range(8))}"


def _history(profile: Profile, rng: random.Random) -> dict:
    """Customer history consistent with the profile's category.

    These branches ARE the category definitions from Doc §3 Stage 2, read backwards.
    If this function and the Phase 6 classifier ever disagree, one of them is wrong.
    """
    label = profile.label

    if label is ReasonCategory.OVERSIGHT:
        # "Clean payment history, first time overdue."
        total = rng.randint(3, 20)
        return {
            "customer_total_invoices": total,
            "customer_invoices_paid_late": 0,
            "customer_invoices_defaulted": 0,
            "customer_broken_promises": 0,
        }

    if label is ReasonCategory.CASH_CONSTRAINED:
        # "Has paid late before, but has always eventually paid in full."
        total = rng.randint(6, 30)
        late = rng.randint(2, max(2, min(8, total - 1)))
        return {
            "customer_total_invoices": total,
            "customer_invoices_paid_late": late,
            "customer_invoices_defaulted": 0,  # the signal that separates this from unresponsive
            "customer_broken_promises": rng.randint(0, 2),
        }

    if label is ReasonCategory.UNRESPONSIVE:
        # Has genuinely defaulted before, and does not engage.
        total = rng.randint(5, 25)
        late = rng.randint(3, max(3, min(12, total - 1)))
        defaulted = rng.randint(1, max(1, min(4, late)))
        return {
            "customer_total_invoices": total,
            "customer_invoices_paid_late": late,
            "customer_invoices_defaulted": defaulted,
            "customer_broken_promises": rng.randint(1, 4),
        }

    # DISPUTE_LIKELY: history is unremarkable; the dispute note is what classifies it.
    total = rng.randint(4, 20)
    late = rng.randint(0, min(3, total))
    return {
        "customer_total_invoices": total,
        "customer_invoices_paid_late": late,
        "customer_invoices_defaulted": 0,
        "customer_broken_promises": 0,
    }


def _amount(profile: Profile, avg_inr: int, rng: random.Random) -> Decimal:
    """Invoice size relative to the customer's typical order.

    Cash-constrained cases skew large: an unusually big invoice against a customer who
    pays late is the shape of a genuine cash-flow problem, and Doc §3 lists relative
    invoice size as a diagnosis signal.
    """
    if profile.label is ReasonCategory.CASH_CONSTRAINED:
        multiplier = Decimal(str(round(rng.uniform(1.5, 3.0), 2)))
    else:
        multiplier = Decimal(str(round(rng.uniform(0.6, 1.4), 2)))
    raw = Decimal(avg_inr) * multiplier
    rounded = Decimal(int(raw / 500) * 500)  # round to a plausible ₹500 boundary
    return min(rounded, Decimal(MAX_INVOICE_INR))


REPLY_TEMPLATES = {
    "promise": [
        "Sorry for the delay — cash is tight this month. I'll clear this by the {day}th.",
        "Payment is scheduled from our side. You'll have it by the {day}th.",
        "Apologies, we've been short this cycle. Can pay by the {day}th without fail.",
    ],
    "complaint": [
        "We were billed for 12 units but received 9. Please check before we pay.",
        "This invoice doesn't match the PO we signed. Sending our copy — please review.",
        "There's a pricing mismatch here versus what was agreed. Holding payment for now.",
    ],
}


def generate(count: int, seed: int, *, include_boundaries: bool) -> tuple[list[dict], dict]:
    rng = random.Random(seed)
    today = date.today()
    rows: list[dict] = []
    replies: dict[str, dict | None] = {}

    weights = [p.weight for p in PROFILES]

    for i in range(count):
        profile = rng.choices(PROFILES, weights=weights, k=1)[0]

        if include_boundaries and i < len(BOUNDARY_DAYS):
            days_over = BOUNDARY_DAYS[i]
        elif profile.label is ReasonCategory.UNRESPONSIVE:
            # By definition: no reply after Tier 2, so it must be past Tier 2.
            days_over = rng.randint(TIER_2_DAYS_OVERDUE + 2, 40)
        elif profile.label is ReasonCategory.OVERSIGHT:
            days_over = rng.randint(TIER_1_DAYS_OVERDUE, TIER_2_DAYS_OVERDUE - 1)
        else:
            days_over = rng.randint(TIER_1_DAYS_OVERDUE, 35)

        history = _history(profile, rng)
        avg_inr = rng.choice([5_000, 8_000, 11_000, 14_000, 16_000])
        amount = _amount(profile, avg_inr, rng)

        terms = rng.choice([15, 30, 45])
        due = today - timedelta(days=days_over)
        issued = due - timedelta(days=terms)

        company = _company(rng)
        number = f"INV-{2000 + i}"

        rows.append(
            {
                "invoice_number": number,
                "customer_name": company,
                "customer_email": _email(company, rng),
                "customer_phone": _phone(rng),
                "amount_inr": str(amount),
                "issued_at": issued.isoformat(),
                "due_at": due.isoformat(),
                "terms_days": terms,
                **history,
                "customer_avg_invoice_inr": str(avg_inr),
                "has_prior_dispute_note": profile.label is ReasonCategory.DISPUTE_LIKELY,
                # --- eval-only, stripped at ingestion ---
                "ground_truth_reason": profile.label.value,
                "ground_truth_outcome": profile.outcome,
                "gen_days_overdue": days_over,
            }
        )

        # Reply fixtures drive Phase 6 promise extraction and Phase 11 simulation
        # without needing a live inbox.
        if profile.label is ReasonCategory.DISPUTE_LIKELY:
            replies[number] = {
                "day_offset": rng.randint(2, 5),
                "body": rng.choice(REPLY_TEMPLATES["complaint"]),
                "kind": "complaint",
            }
        elif profile.outcome == "needs_multiple" and rng.random() < 0.7:
            replies[number] = {
                "day_offset": rng.randint(3, 6),
                "body": rng.choice(REPLY_TEMPLATES["promise"]).format(
                    day=rng.choice([15, 20, 25, 28])
                ),
                "kind": "promise",
            }
        elif profile.label is ReasonCategory.UNRESPONSIVE:
            replies[number] = None  # explicit: this customer does not reply
        elif rng.random() < 0.2:
            replies[number] = {
                "day_offset": rng.randint(2, 5),
                "body": rng.choice(REPLY_TEMPLATES["promise"]).format(day=rng.choice([18, 22])),
                "kind": "promise",
            }

    return rows, replies


FIELDNAMES = [
    "invoice_number",
    "customer_name",
    "customer_email",
    "customer_phone",
    "amount_inr",
    "issued_at",
    "due_at",
    "terms_days",
    "customer_total_invoices",
    "customer_invoices_paid_late",
    "customer_invoices_defaulted",
    "customer_broken_promises",
    "customer_avg_invoice_inr",
    "has_prior_dispute_note",
    "ground_truth_reason",
    "ground_truth_outcome",
    "gen_days_overdue",
]


def write_csv(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def summarise(name: str, rows: list[dict]) -> None:
    from collections import Counter

    reasons = Counter(r["ground_truth_reason"] for r in rows)
    outcomes = Counter(r["ground_truth_outcome"] for r in rows)
    total = sum(Decimal(r["amount_inr"]) for r in rows)
    print(f"\n{name}: {len(rows)} invoices, ₹{total:,.0f} total")
    print("  reasons: " + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    print("  outcomes: " + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo-count", type=int, default=60)
    ap.add_argument("--eval-count", type=int, default=150)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    demo_rows, demo_replies = generate(args.demo_count, args.seed, include_boundaries=True)
    write_csv(DATA_DIR / "invoices_demo.csv", demo_rows)
    (DATA_DIR / "replies_fixture.json").write_text(json.dumps(demo_replies, indent=2) + "\n")
    summarise("demo", demo_rows)

    # A different seed, so the held-out set shares no rows with the demo set.
    eval_rows, eval_replies = generate(args.eval_count, args.seed + 1, include_boundaries=False)
    for r in eval_rows:
        r["invoice_number"] = r["invoice_number"].replace("INV-", "EVL-")
    eval_replies = {k.replace("INV-", "EVL-"): v for k, v in eval_replies.items()}
    write_csv(DATA_DIR / "invoices_eval.csv", eval_rows)
    (DATA_DIR / "replies_eval.json").write_text(json.dumps(eval_replies, indent=2) + "\n")
    summarise("eval", eval_rows)

    print(f"\nWritten to {DATA_DIR}")


if __name__ == "__main__":
    main()
