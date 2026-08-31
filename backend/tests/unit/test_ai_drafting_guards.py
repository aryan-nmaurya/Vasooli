"""The converse check: nothing financial in a draft that we did not supply.

`verify_figures` asks whether the correct figures are present. That is the easy half.
The half that matters is whether anything *else* financial is present too — a draft can
carry the right amount, the right invoice number and the right link, and also a late fee
nobody agreed to or an address to send the money somewhere else.

Each case below passed both checks before. They are the shapes that slip between a
money regex that needs a currency marker and a digit regex that needs four digits.
"""

import pytest

from app.ai.drafting import DraftInputs, find_invented_figures, template_draft, verify_figures

INPUTS = DraftInputs(
    merchant_name="Acme Steel",
    customer_name="Nova Retail",
    invoice_number="INV-3001",
    outstanding_paise=4_200_000,
    due_date="01 August 2026",
    days_overdue=30,
    payment_url="https://rzp.io/l/abc123",
    reason_explanation="",
    tier=2,
)

CORRECT = (
    "Invoice INV-3001\n"
    "Rs 42,000 is outstanding, due 01 August 2026, now 30 days overdue.\n"
    "You can pay here: https://rzp.io/l/abc123"
)


def test_a_correct_draft_is_not_flagged():
    assert verify_figures(CORRECT, INPUTS) == []
    assert find_invented_figures(CORRECT, INPUTS) == []


@pytest.mark.parametrize("tier", [1, 2, 3])
@pytest.mark.parametrize("payment_url", ["https://rzp.io/l/abc123", ""])
def test_the_deterministic_templates_always_pass(tier, payment_url):
    """The fallback is what a rejected draft falls back *to*; it must never be rejected."""
    inputs = DraftInputs(**{**INPUTS.__dict__, "tier": tier, "payment_url": payment_url})
    draft = template_draft(inputs)
    text = f"{draft.subject}\n{draft.body}"
    assert verify_figures(text, inputs) == []
    assert find_invented_figures(text, inputs) == []


@pytest.mark.parametrize(
    ("addition", "expected"),
    [
        # A charge small enough to have no grouping and fewer than four digits fell
        # between the currency regex and the long-number regex.
        ("A processing charge of 500 applies.", "extra_charge"),
        ("A late fee of 250 will be added.", "extra_charge"),
        ("A penalty of 99 is payable.", "extra_charge"),
        # A rate names no rupee figure at all, so no amount check ever saw it.
        ("Interest accrues at 2% per month.", "extra_rate"),
        ("A late payment charge of 1.5 percent applies.", "extra_rate"),
        # Somewhere else to send the money — the highest-consequence miss, because it
        # is the shape a redirected payment takes.
        ("You may also remit to accounts@attacker.example", "alternative_payment_destination"),
        ("Or pay via UPI to acme@okhdfc", "alternative_payment_destination"),
        ("Settle at pay-now.example/settle", "alternative_payment_destination"),
    ],
)
def test_invented_financial_content_is_caught(addition, expected):
    text = f"{CORRECT}\n{addition}"
    assert verify_figures(text, INPUTS) == [], "the required figures are all still present"
    assert expected in find_invented_figures(text, INPUTS), (
        "a draft carrying every correct figure AND this one was accepted; it is the "
        "draft that looks right on a skim"
    )


@pytest.mark.parametrize(
    "addition",
    [
        "A late fee of Rs 5,000 applies.",
        "Also see https://evil.example/pay",
        "Ref PO-99887",
        "Call us on 98765 43210",
    ],
)
def test_the_checks_that_already_worked_still_work(addition):
    assert find_invented_figures(f"{CORRECT}\n{addition}", INPUTS) != []
