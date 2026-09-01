import pytest
from cryptography.fernet import InvalidToken

from app.services.billing import PLAN_CATALOG
from app.services.payment_connections import decrypt_secret, encrypt_secret
from app.services.plans import PLANS, Feature


def test_plan_catalog_matches_published_pricing():
    """Seats are people who can sign in: 1 on Starter, 5 on Growth, 15 on Scale."""
    assert PLAN_CATALOG == {
        "starter": (199_900, 100, 1),
        "growth": (599_900, 500, 5),
        "scale": (1_499_900, 2_000, 15),
    }


def test_plan_features_are_cumulative_up_the_catalogue():
    """Each tier must contain everything the tier below it sells.

    The pricing page says "Everything in Starter" and "Everything in Growth". If a
    feature were ever dropped from a higher plan, an upgrade would silently remove a
    capability the merchant was already paying for.
    """
    for lower, higher in zip(PLANS, PLANS[1:], strict=False):
        assert lower.features <= higher.features, (
            f"{higher.name} is missing features that {lower.name} includes"
        )
        assert higher.included_seats > lower.included_seats
        assert higher.included_active_invoices > lower.included_active_invoices
        assert higher.amount_paise > lower.amount_paise


def test_every_feature_is_sold_by_some_plan():
    """A gate nobody can pass is a dead end, not a paywall."""
    sold = set().union(*(plan.features for plan in PLANS))
    assert set(Feature) == sold


def test_connection_secrets_are_encrypted_and_round_trip():
    token = encrypt_secret("rzp_secret_value")
    assert token != "rzp_secret_value"
    assert decrypt_secret(token) == "rzp_secret_value"


def test_tampered_connection_secret_is_rejected():
    token = encrypt_secret("rzp_secret_value")
    with pytest.raises(InvalidToken):
        decrypt_secret(token[:-1] + ("A" if token[-1] != "A" else "B"))
