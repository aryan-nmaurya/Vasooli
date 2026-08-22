"""Money is integer paise, formatted in the Indian numbering system."""

from decimal import Decimal

import pytest

from app.core.money import format_inr, paise_to_rupees, rupees_to_paise


@pytest.mark.parametrize(
    ("rupees", "paise"),
    [("42000", 4_200_000), ("18500.50", 1_850_050), (75000, 7_500_000), ("0.01", 1)],
)
def test_rupees_to_paise(rupees, paise):
    assert rupees_to_paise(rupees) == paise


def test_float_input_is_rejected_not_silently_rounded():
    """The caller has already lost precision; failing loudly beats guessing."""
    with pytest.raises(TypeError, match="float is not accepted"):
        rupees_to_paise(42000.10)


def test_round_trip_is_lossless():
    for paise in (1, 99, 4_200_000, 1_850_050, 18_20_000_00):
        assert rupees_to_paise(paise_to_rupees(paise)) == paise


@pytest.mark.parametrize(
    ("paise", "shown"),
    [
        (6_40_000_00, "₹6,40,000"),  # Doc §7 dashboard figure
        (2_15_000_00, "₹2,15,000"),
        (4_200_000, "₹42,000"),
        (1_850_050, "₹18,500.50"),
        (7_500_000, "₹75,000"),
        (100, "₹1"),
        (0, "₹0"),
        (1_00_00_00_000, "₹1,00,00,000"),  # one crore
    ],
)
def test_indian_grouping(paise, shown):
    """Lakh/crore grouping — 6,40,000 not 640,000. toLocaleString('en-US') is wrong here."""
    assert format_inr(paise) == shown


def test_negative_amounts_keep_the_sign_outside_the_symbol():
    assert format_inr(-4_200_000) == "-₹42,000"


def test_decimal_arithmetic_stays_exact():
    total = sum(rupees_to_paise(Decimal("0.10")) for _ in range(3))
    assert total == rupees_to_paise("0.30")  # the float version of this fails
