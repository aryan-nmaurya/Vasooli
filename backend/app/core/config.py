"""Typed application settings.

Every secret and tunable enters the app here. Required fields have no default, so a
missing env var fails at import time with a readable pydantic error rather than at
2am in a webhook handler.
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = "local"
    log_level: str = "INFO"
    log_json: bool = False  # True in deployed environments

    # --- Database ---
    database_url: str
    db_echo: bool = False

    # --- Razorpay (Payment Links) ---
    # Smart Collect / Virtual Accounts is NOT used: Razorpay confirmed it is
    # unavailable for this merchant's business type. See README.
    razorpay_key_id: str
    razorpay_key_secret: str
    razorpay_webhook_secret: str
    #: Minimum gap between Razorpay API calls. Test mode rate-limits aggressively —
    #: a 60-invoice batch fired flat out trips it within a few requests.
    razorpay_min_request_interval_seconds: float = 1.5

    # --- Gemini via Google AI Studio ---
    # Model IDs are config, not literals: a retired or mistyped ID is a .env edit.
    google_api_key: str
    gemini_primary_model: str = "gemini-3.7-flash"
    gemini_fallback_model: str = "gemini-3.6-flash"
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 2

    # --- Email ---
    resend_api_key: str
    sendgrid_api_key: str | None = None
    #: Resend gives every account this sender without any DNS setup. It can only
    #: deliver to the address the account was registered with, which is exactly the
    #: behaviour we want while the customer list is synthetic.
    email_from: str = "Vasooli <onboarding@resend.dev>"
    email_reply_to_domain: str = "example.com"
    email_dry_run: bool = True

    #: When set, every reminder is delivered here instead of to the customer, with the
    #: intended recipient shown in the subject. The synthetic ledger contains 52 fake
    #: domains, so nothing would arrive anyway — but the real reason is that this makes
    #: it impossible to email a live person by accident if a real address ever lands in
    #: the data. Required before live sending is allowed.
    email_redirect_to: str | None = None

    # --- Ops ---
    scheduler_enabled: bool = True
    admin_api_key: str
    #: Password for the dashboard login. Exchanged once for a signed session cookie;
    #: never stored by the browser and never sent again after login.
    dashboard_password: str = ""
    #: HMAC key for session tokens. Rotating it invalidates every live session, which
    #: is the intended way to force everyone out.
    session_secret: str = ""
    # NoDecode: without it the dotenv source tries to JSON-parse this field before
    # our validator runs, so a comma-separated CORS_ORIGINS would be a hard error.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # --- Demo controls (Phase 8) ---
    demo_time_offset_days: int = 0

    @field_validator("database_url")
    @classmethod
    def _require_psycopg_driver(cls, v: str) -> str:
        """Pin the psycopg3 driver explicitly.

        A bare postgresql:// URL makes SQLAlchemy reach for psycopg2, which is not a
        dependency here. Railway and Neon both hand out bare URLs, so normalize.
        """
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+psycopg://", 1)
        elif v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+psycopg://", 1)
        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def assert_production_safe(self) -> None:
        """Guards that must hold before this process serves real traffic.

        Called from the app lifespan. Keeps demo affordances from riding to prod.
        """
        if not self.is_production:
            return
        if self.demo_time_offset_days != 0:
            raise RuntimeError(
                f"DEMO_TIME_OFFSET_DAYS must be 0 in production (got {self.demo_time_offset_days})"
            )
        if self.admin_api_key in {"", "changeme", "local-dev-key"}:
            raise RuntimeError("ADMIN_API_KEY must be set to a real secret in production")
        if not self.dashboard_password or len(self.dashboard_password) < 12:
            raise RuntimeError(
                "DASHBOARD_PASSWORD must be set to at least 12 characters in production"
            )
        if not self.session_secret or len(self.session_secret) < 32:
            raise RuntimeError(
                "SESSION_SECRET must be set to at least 32 random characters in production"
            )

    def assert_safe_to_send(self) -> None:
        """Refuse to send live mail without a redirect target.

        The customer list is synthetic. Sending live, unredirected mail from it means
        emailing 52 domains nobody owns — and if a real address ever slips into the
        ledger, a stranger receives a debt reminder. Turning off dry-run has to be a
        deliberate act with a stated destination.
        """
        if self.email_dry_run:
            return
        if not self.email_redirect_to and not self.is_production:
            raise RuntimeError(
                "EMAIL_DRY_RUN is false but EMAIL_REDIRECT_TO is not set.\n"
                "Set EMAIL_REDIRECT_TO to your own address so reminders go to your "
                "inbox, or leave EMAIL_DRY_RUN=true."
            )


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]  # values come from env
    except ValidationError as exc:
        missing = [
            ".".join(str(p) for p in err["loc"]) for err in exc.errors() if err["type"] == "missing"
        ]
        hint = (
            f"\n\nMissing required environment variables: {', '.join(missing)}"
            "\nCopy .env.example to .env and fill them in."
            if missing
            else ""
        )
        raise ConfigurationError(f"Invalid application configuration:\n{exc}{hint}") from exc


settings = get_settings()
