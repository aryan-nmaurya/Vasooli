import pytest
from cryptography.fernet import InvalidToken

from app.services.billing import PLAN_CATALOG
from app.services.payment_connections import decrypt_secret, encrypt_secret


def test_plan_catalog_matches_published_pricing():
    assert PLAN_CATALOG == {
        "starter": (199_900, 100, 5),
        "growth": (599_900, 500, 15),
        "scale": (1_499_900, 2_000, 50),
    }


def test_connection_secrets_are_encrypted_and_round_trip():
    token = encrypt_secret("rzp_secret_value")
    assert token != "rzp_secret_value"
    assert decrypt_secret(token) == "rzp_secret_value"


def test_tampered_connection_secret_is_rejected():
    token = encrypt_secret("rzp_secret_value")
    with pytest.raises(InvalidToken):
        decrypt_secret(token[:-1] + ("A" if token[-1] != "A" else "B"))
