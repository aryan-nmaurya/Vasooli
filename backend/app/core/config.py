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

    # --- Razorpay (Smart Collect / Virtual Accounts) ---
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
    email_from: str = "vasooli@example.com"
    email_reply_to_domain: str = "example.com"
    email_dry_run: bool = True  # stays True until Phase 7 exit criteria pass

    # --- Ops ---
    scheduler_enabled: bool = True
    admin_api_key: str
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
