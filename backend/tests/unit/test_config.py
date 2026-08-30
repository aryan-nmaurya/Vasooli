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
    for key in [*REQUIRED, "ENVIRONMENT", "DEMO_TIME_OFFSET_DAYS", "CORS_ORIGINS"]:
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
