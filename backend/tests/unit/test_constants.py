"""The locked schema. If these fail, the spec and the code have diverged.

Doc §2 Stage 2 (reason categories) and §3 Stage 3 (cadence) are the source of truth.
"""

from app.core import constants as C


def test_cadence_is_exactly_3_10_21():
    assert C.TIER_SCHEDULE == {1: 3, 2: 10, 3: 21}
    assert C.TIER_1_DAYS_OVERDUE == 3
    assert C.TIER_2_DAYS_OVERDUE == 10
    assert C.TIER_3_DAYS_OVERDUE == 21


def test_cadence_is_strictly_increasing():
    days = [C.TIER_SCHEDULE[t] for t in sorted(C.TIER_SCHEDULE)]
    assert days == sorted(days)
    assert len(set(days)) == len(days)


def test_reminder_cap_matches_tier_count():
    # "Maximum of 3 automated reminders before mandatory human handoff" — Doc §3.
    assert C.MAX_AUTOMATED_REMINDERS == len(C.TIER_SCHEDULE) == 3


def test_cooldown_prevents_same_week_contact():
    # Doc §3: "no same-week repeated contact".
    assert C.MIN_COOLDOWN_DAYS >= 7
    # A cooldown longer than the tier-1 to tier-2 gap would make tier 2 unreachable.
    assert C.MIN_COOLDOWN_DAYS <= C.TIER_2_DAYS_OVERDUE - C.TIER_1_DAYS_OVERDUE


def test_four_reason_categories_exact():
    assert {r.value for r in C.ReasonCategory} == {
        "oversight",
        "cash_constrained",
        "dispute_likely",
        "unresponsive",
    }


def test_every_tier_has_exactly_one_tone():
    assert set(C.TONE_FOR_TIER) == set(C.TIER_SCHEDULE)
    assert C.TONE_FOR_TIER == {1: C.Tone.POLITE, 2: C.Tone.FIRM, 3: C.Tone.FINAL}


def test_terminal_statuses_are_final():
    assert C.InvoiceStatus.RECOVERED in C.TERMINAL_STATUSES
    assert C.InvoiceStatus.WRITTEN_OFF in C.TERMINAL_STATUSES
    assert C.InvoiceStatus.CHASING not in C.TERMINAL_STATUSES


def test_constants_are_not_duplicated_elsewhere():
    """Guard the 'define once, import everywhere' rule from the plan (§0.1).

    Cadence day-counts must appear as literals only in constants.py.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    pattern = re.compile(r"\b(TIER_\d_DAYS_OVERDUE\s*=\s*\d+)")
    offenders = [
        p for p in root.rglob("*.py") if p.name != "constants.py" and pattern.search(p.read_text())
    ]
    assert not offenders, f"cadence constants redefined outside constants.py: {offenders}"
