"""Money handling. Integer paise everywhere.

Floats are banned on money paths. `0.1 + 0.2 != 0.3` is a rounding curiosity in most
code; in reconciliation it is an invoice that never closes because the balance lands a
paisa short of `amount_expected`. Razorpay's API is already denominated in paise, so
integers are also the format that crosses the wire unchanged.

Enforced by tests/architecture/test_layering.py: `float(` is banned in app code.
"""

from decimal import ROUND_HALF_UP, Decimal

PAISE_PER_RUPEE = 100


def rupees_to_paise(rupees: Decimal | int | str) -> int:
    """Convert rupees to integer paise, rounding half-up at the paisa.

    Accepts str/int/Decimal only. Passing a float is a bug — the caller has already
    lost precision by the time we see it — so it raises rather than silently rounding.
    """
    if isinstance(rupees, float):
        raise TypeError(
            f"float is not accepted on money paths; pass Decimal, int, or str (got {rupees!r})"
        )
    amount = Decimal(str(rupees)) * PAISE_PER_RUPEE
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def paise_to_rupees(paise: int) -> Decimal:
    """Exact rupee value of an integer paise amount."""
    return (Decimal(paise) / PAISE_PER_RUPEE).quantize(Decimal("0.01"))


def format_inr(paise: int) -> str:
    """Format paise for display using the Indian numbering system.

    Groups the last three digits, then in twos: 6400000 paise -> "₹64,000".
    Whole rupee amounts drop the decimals; anything else keeps two places.
    The frontend mirrors this in frontend/lib/money.ts — keep the two in step.
    """
    rupees = paise_to_rupees(abs(paise))
    whole, _, frac = str(rupees).partition(".")

    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])

    sign = "-" if paise < 0 else ""
    suffix = "" if frac == "00" else f".{frac}"
    return f"{sign}₹{whole}{suffix}"
