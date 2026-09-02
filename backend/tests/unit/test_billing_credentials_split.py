"""Subscription billing and the demo must not share one Razorpay credential.

The platform key serves the DEMO: `razorpay_client_for_merchant` returns it for any
demo merchant, and the dashboard shows a hard-coded "Test mode" badge beside it.
Subscription billing genuinely needs live credentials. Pointing the single shared key
at a live account would put the guided demo on live rails while it still claimed to
be in test mode — so the two are split.
"""

import pytest

from app.core.config import Settings, settings


def test_billing_falls_back_to_the_platform_key_when_not_split():
    """A deployment that has not split them yet must behave exactly as before."""
    assert settings.razorpay_billing_key_id is None
    assert settings.effective_billing_key_id == settings.razorpay_key_id
    assert settings.effective_billing_key_secret == settings.razorpay_key_secret
    assert settings.effective_billing_webhook_secret == settings.razorpay_webhook_secret


def test_split_credentials_are_preferred(monkeypatch):
    monkeypatch.setattr(settings, "razorpay_billing_key_id", "rzp_live_BILLING", raising=False)
    monkeypatch.setattr(settings, "razorpay_billing_key_secret", "billing-secret", raising=False)
    monkeypatch.setattr(
        settings, "razorpay_billing_webhook_secret", "billing-webhook", raising=False
    )

    assert settings.effective_billing_key_id == "rzp_live_BILLING"
    assert settings.effective_billing_key_secret == "billing-secret"
    assert settings.effective_billing_webhook_secret == "billing-webhook"
    # The demo key is untouched by any of that.
    assert settings.razorpay_key_id.startswith("rzp_test_")


def _production_settings(**overrides):
    base = dict(
        environment="production",
        database_url="postgresql+psycopg://u:p@localhost:5432/db",
        admin_api_key="a" * 40,
        session_secret="s" * 40,
        credential_encryption_key="k" * 44,
        razorpay_key_id="rzp_test_demo",
        razorpay_key_secret="demo-secret",
        razorpay_webhook_secret="demo-webhook",
        email_dry_run=True,
        # Flags the production guard checks before it reaches the Razorpay ones.
        allow_simulated_replies=False,
        demo_controls_enabled=False,
        reviewer_access_enabled=False,
        live_registration_enabled=False,
        scheduler_enabled=False,
    )
    base.update(overrides)
    return base


def test_a_live_demo_key_is_refused_outright():
    """The demo transacts on this key. It must never be live, flag or no flag."""
    cfg = Settings(
        **_production_settings(razorpay_key_id="rzp_live_OOPS", allow_live_razorpay=True)
    )
    with pytest.raises(RuntimeError) as exc:
        cfg.assert_production_safe()
    assert "DEMO credential" in str(exc.value)
    assert "RAZORPAY_BILLING_KEY_ID" in str(exc.value), "the error must say where it belongs"


def test_live_billing_credentials_still_need_the_flag():
    cfg = Settings(
        **_production_settings(
            razorpay_billing_key_id="rzp_live_BILLING", allow_live_razorpay=False
        )
    )
    with pytest.raises(RuntimeError, match="ALLOW_LIVE_RAZORPAY"):
        cfg.assert_production_safe()


def test_live_billing_with_a_test_demo_key_is_the_intended_shape():
    """Real subscription money, demo still safely in test mode."""
    cfg = Settings(
        **_production_settings(razorpay_billing_key_id="rzp_live_BILLING", allow_live_razorpay=True)
    )
    cfg.assert_production_safe()  # must not raise
    assert cfg.effective_billing_key_id == "rzp_live_BILLING"
    assert cfg.razorpay_key_id == "rzp_test_demo"
