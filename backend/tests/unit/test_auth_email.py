from app.core.config import settings
from app.integrations.email.base import SendResult
from app.services.auth_email import AuthEmailError, send_auth_email


class CapturingProvider:
    name = "capture"

    def __init__(self, *, sent: bool = True):
        self.sent = sent
        self.message = None

    def send(self, **message):
        self.message = message
        return SendResult(
            sent=self.sent,
            provider=self.name,
            error=None if self.sent else "provider unavailable",
            retryable=not self.sent,
        )


def test_verification_email_uses_the_public_https_origin(monkeypatch):
    monkeypatch.setattr(settings, "environment", "staging")
    monkeypatch.setattr(settings, "email_dry_run", False)
    monkeypatch.setattr(settings, "frontend_public_url", "https://app.vasooli.test/")
    provider = CapturingProvider()

    send_auth_email(
        purpose="verify_email",
        email="owner@example.test",
        token="secret/token+value",
        provider=provider,
    )

    assert provider.message["to"] == "owner@example.test"
    assert (
        "https://app.vasooli.test/verify-email?token=secret%2Ftoken%2Bvalue"
        in provider.message["text"]
    )
    assert "secret/token+value" not in provider.message["text"]


def test_password_reset_provider_failure_is_explicit(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "email_dry_run", False)
    monkeypatch.setattr(settings, "frontend_public_url", "https://app.vasooli.test")

    try:
        send_auth_email(
            purpose="password_reset",
            email="owner@example.test",
            token="t" * 32,
            provider=CapturingProvider(sent=False),
        )
    except AuthEmailError as exc:
        assert "provider unavailable" in str(exc)
    else:
        raise AssertionError("provider rejection must fail closed")


def test_local_identity_flow_never_calls_the_network(monkeypatch):
    monkeypatch.setattr(settings, "environment", "local")
    provider = CapturingProvider()

    send_auth_email(
        purpose="verify_email",
        email="owner@example.test",
        token="t" * 32,
        provider=provider,
    )

    assert provider.message is None
