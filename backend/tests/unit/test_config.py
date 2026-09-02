"""Configuration must fail loudly, not silently."""

import pytest
from pydantic import ValidationError

from app.core.config import ConfigurationError, Settings, get_settings

REQUIRED = {
    "DATABASE_URL": "postgresql://localhost:5432/vasooli",
    "RAZORPAY_KEY_ID": "rzp_test_x",
    "RAZORPAY_KEY_SECRET": "s",
    "RAZORPAY_WEBHOOK_SECRET": "w",
    "GOOGLE_API_KEY": "g",
    "RESEND_API_KEY": "r",
    "ADMIN_API_KEY": "a",
}


def _isolated_env(monkeypatch, overrides: dict[str, str], drop: str | None = None):
    """Build a clean env from REQUIRED, ignoring the developer's real .env file."""
    for key in [
        *REQUIRED,
        "ENVIRONMENT",
        "DEMO_TIME_OFFSET_DAYS",
        "CORS_ORIGINS",
        "PROCESS_ROLE",
    ]:
        monkeypatch.delenv(key, raising=False)
    values = {**REQUIRED, **overrides}
    if drop:
        values.pop(drop)
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    # _env_file=None stops pydantic-settings from backfilling from the local .env.
    return lambda: Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_var_refuses_to_load(monkeypatch, missing):
    build = _isolated_env(monkeypatch, {}, drop=missing)
    with pytest.raises(ValidationError) as exc:
        build()
    assert missing.lower() in str(exc.value).lower()


def test_get_settings_raises_readable_configuration_error(monkeypatch):
    for key in REQUIRED:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("app.core.config.Settings", lambda: Settings(_env_file=None))
    get_settings.cache_clear()
    with pytest.raises(ConfigurationError) as exc:
        get_settings()
    assert "Missing required environment variables" in str(exc.value)
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgres://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        ("postgresql://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        ("postgresql+psycopg://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
    ],
)
def test_database_url_driver_is_normalized(monkeypatch, given, expected):
    """Railway and Neon hand out bare URLs that would reach for psycopg2."""
    build = _isolated_env(monkeypatch, {"DATABASE_URL": given})
    assert build().database_url == expected


def test_cors_origins_accepts_comma_separated_string(monkeypatch):
    build = _isolated_env(monkeypatch, {"CORS_ORIGINS": "https://a.dev, https://b.dev"})
    assert build().cors_origins == ["https://a.dev", "https://b.dev"]


def test_production_rejects_demo_time_offset(monkeypatch):
    build = _isolated_env(
        monkeypatch,
        {"ENVIRONMENT": "production", "DEMO_TIME_OFFSET_DAYS": "14", "ADMIN_API_KEY": "real"},
    )
    with pytest.raises(RuntimeError, match="DEMO_TIME_OFFSET_DAYS must be 0"):
        build().assert_production_safe()


def test_production_rejects_placeholder_admin_key(monkeypatch):
    build = _isolated_env(
        monkeypatch, {"ENVIRONMENT": "production", "ADMIN_API_KEY": "local-dev-key"}
    )
    with pytest.raises(RuntimeError, match="ADMIN_API_KEY"):
        build().assert_production_safe()


def test_local_environment_allows_demo_offset(monkeypatch):
    build = _isolated_env(monkeypatch, {"DEMO_TIME_OFFSET_DAYS": "14"})
    build().assert_production_safe()  # must not raise


def test_email_dry_run_defaults_to_true(monkeypatch):
    """Guards against accidentally emailing synthetic customers before live-email approval."""
    build = _isolated_env(monkeypatch, {})
    assert build().email_dry_run is True


def test_migration_process_role_is_valid(monkeypatch):
    build = _isolated_env(monkeypatch, {"PROCESS_ROLE": "migrate"})
    assert build().process_role == "migrate"


def test_production_refuses_live_registration_without_identity_email(monkeypatch):
    build = _isolated_env(
        monkeypatch,
        {
            "ENVIRONMENT": "production",
            "ADMIN_API_KEY": "production-admin-key",
            "SESSION_SECRET": "s" * 32,
            "CREDENTIAL_ENCRYPTION_KEY": "separate-encryption-key",
            "LIVE_REGISTRATION_ENABLED": "true",
            "EMAIL_DRY_RUN": "true",
            "FRONTEND_PUBLIC_URL": "https://app.example.test",
        },
    )
    with pytest.raises(RuntimeError, match="EMAIL_DRY_RUN=false"):
        build().assert_production_safe()


def test_production_refuses_insecure_identity_links(monkeypatch):
    build = _isolated_env(
        monkeypatch,
        {
            "ENVIRONMENT": "production",
            "ADMIN_API_KEY": "production-admin-key",
            "SESSION_SECRET": "s" * 32,
            "CREDENTIAL_ENCRYPTION_KEY": "separate-encryption-key",
            "LIVE_REGISTRATION_ENABLED": "true",
            "EMAIL_DRY_RUN": "false",
            "FRONTEND_PUBLIC_URL": "http://app.example.test",
        },
    )
    with pytest.raises(RuntimeError, match="https://"):
        build().assert_production_safe()


# --- Demo controls in production -------------------------------------------------
#
# The demo clock is process-global: advancing it shifts `utcnow()` for every tenant,
# not only the demo's. Production therefore refuses it unless a second, separate flag
# says the operator has accepted that. These pin both halves, because a guard that can
# be satisfied by the flag it guards is not a guard.

_PROD = {
    "ENVIRONMENT": "production",
    "ADMIN_API_KEY": "a-real-admin-secret",
    "SESSION_SECRET": "x" * 40,
    "CREDENTIAL_ENCRYPTION_KEY": "k" * 44,
}


def test_production_refuses_demo_controls_without_the_override(monkeypatch):
    build = _isolated_env(monkeypatch, {**_PROD, "DEMO_CONTROLS_ENABLED": "true"})
    with pytest.raises(RuntimeError, match="DEMO_CONTROLS_ENABLED must be false"):
        build().assert_production_safe()


def test_production_allows_demo_controls_with_the_deliberate_override(monkeypatch):
    build = _isolated_env(
        monkeypatch,
        {
            **_PROD,
            "DEMO_CONTROLS_ENABLED": "true",
            "ALLOW_DEMO_CONTROLS_IN_PRODUCTION": "true",
        },
    )
    build().assert_production_safe()  # must not raise


def test_the_override_does_not_also_unlock_simulated_replies(monkeypatch):
    """Fabricated customer statements stay banned however the demo clock is set.

    Moving time runs the real code against a later date. A simulated reply invents
    evidence, which no deployment flag should be able to buy.
    """
    build = _isolated_env(
        monkeypatch,
        {
            **_PROD,
            "ALLOW_SIMULATED_REPLIES": "true",
            "ALLOW_DEMO_CONTROLS_IN_PRODUCTION": "true",
        },
    )
    with pytest.raises(RuntimeError, match="ALLOW_SIMULATED_REPLIES"):
        build().assert_production_safe()
