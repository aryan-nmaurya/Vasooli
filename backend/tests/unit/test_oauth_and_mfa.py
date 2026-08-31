import base64
import hashlib
import hmac
import struct

from app.core.config import settings
from app.services.mfa import new_secret, provisioning_uri, verify_code
from app.services.oauth import exchange_zoho_code, razorpay_authorization_url


def _totp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    index = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[index : index + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{value:06d}"


def test_totp_round_trip_and_provisioning_uri():
    secret = new_secret()
    code = _totp(secret, 123456)
    assert verify_code(secret, code, timestamp=123456 * 30)
    assert not verify_code(secret, "000000", timestamp=123456 * 30)
    assert "otpauth://totp/Vasooli" in provisioning_uri(secret, "owner@example.com")


def test_razorpay_authorization_url_is_state_bound(monkeypatch):
    monkeypatch.setattr(settings, "razorpay_oauth_client_id", "client-123")
    monkeypatch.setattr(settings, "razorpay_oauth_scope", "read_write")
    url = razorpay_authorization_url("opaque-state", "https://app.test/callback")
    assert "client_id=client-123" in url
    assert "state=opaque-state" in url
    assert "redirect_uri=https%3A%2F%2Fapp.test%2Fcallback" in url


def test_zoho_exchange_resolves_the_books_organization(monkeypatch):
    import app.services.oauth as mod

    class Response:
        is_error = False

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setattr(settings, "zoho_oauth_client_id", "zoho-client")
    monkeypatch.setattr(settings, "zoho_oauth_client_secret", "zoho-secret")
    monkeypatch.setattr(
        mod.httpx,
        "post",
        lambda *args, **kwargs: Response(
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
                "api_domain": "https://www.zohoapis.in",
            }
        ),
    )
    monkeypatch.setattr(
        mod.httpx,
        "get",
        lambda *args, **kwargs: Response({"organizations": [{"organization_id": "org-123"}]}),
    )

    tokens = exchange_zoho_code("code", "https://api.example.test/callback")

    assert tokens.account_id == "org-123"
    assert tokens.api_domain == "https://www.zohoapis.in"


def test_zoho_exchange_refuses_to_guess_between_organizations(monkeypatch):
    import pytest

    import app.services.oauth as mod
    from app.services.oauth import OAuthExchangeError

    class Response:
        is_error = False

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setattr(settings, "zoho_oauth_client_id", "zoho-client")
    monkeypatch.setattr(settings, "zoho_oauth_client_secret", "zoho-secret")
    monkeypatch.setattr(mod.httpx, "post", lambda *args, **kwargs: Response({"access_token": "a"}))
    monkeypatch.setattr(
        mod.httpx,
        "get",
        lambda *args, **kwargs: Response(
            {"organizations": [{"organization_id": "one"}, {"organization_id": "two"}]}
        ),
    )

    with pytest.raises(OAuthExchangeError, match="exactly one organization"):
        exchange_zoho_code("code", "https://api.example.test/callback")
