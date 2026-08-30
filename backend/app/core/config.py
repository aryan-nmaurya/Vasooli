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
    razorpay_plan_id_starter: str | None = None
    razorpay_plan_id_growth: str | None = None
    razorpay_plan_id_scale: str | None = None
    razorpay_subscriptions_enabled: bool = False
    #: Minimum gap between Razorpay API calls. Test mode rate-limits aggressively —
    #: a 60-invoice batch fired flat out trips it within a few requests.
    razorpay_min_request_interval_seconds: float = 1.5
    razorpay_timeout_seconds: float = 10.0
    allow_live_razorpay: bool = False

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
    resend_inbound_webhook_secret: str = ""
    #: Each Resend webhook endpoint is issued its own signing secret — Resend does not
    #: share one across an account. This is the secret for the delivery/bounce
    #: endpoint specifically, separate from the inbound-mail one above; using the
    #: wrong value here fails signature verification for delivery events even though
    #: inbound mail keeps working, since the two are checked independently.
    resend_delivery_webhook_secret: str = ""
    inbound_email_webhook_secret: str = ""

    #: The demo control that injects a customer reply without an email ever existing.
    #: Defaults OFF, and must stay off wherever the system is presented as real: a
    #: reply that arrived because someone typed it into a box is not evidence that
    #: inbound mail works, and a screen that cannot tell the difference invites the
    #: claim that it does. Turn it on for local development, never in production.
    allow_simulated_replies: bool = False
    email_provider_timeout_seconds: float = 10.0
    email_dry_run: bool = True
    allow_direct_customer_email: bool = False

    #: When set, every reminder is delivered here instead of to the customer, with the
    #: intended recipient shown in the subject. The synthetic ledger contains 52 fake
    #: domains, so nothing would arrive anyway — but the real reason is that this makes
    #: it impossible to email a live person by accident if a real address ever lands in
    #: the data. Required before live sending is allowed.
    email_redirect_to: str | None = None

    # --- Ops ---
    scheduler_enabled: bool = True
    process_role: Literal["api", "scheduler", "worker"] = "api"
    ops_heartbeat_url: str = ""
    ops_recovery_heartbeat_url: str = ""
    admin_api_key: str
    #: HMAC key for session tokens. Rotating it invalidates every live session, which
    #: is the intended way to force everyone out.
    session_secret: str = ""
    #: Optional Fernet key for connector credentials. Production should source this
    #: from KMS/secret-manager material rather than reusing the session signing key.
    credential_encryption_key: str | None = None
    # NoDecode: without it the dotenv source tries to JSON-parse this field before
    # our validator runs, so a comma-separated CORS_ORIGINS would be a hard error.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # --- Demo controls ---
    demo_time_offset_days: int = 0

    #: Enables the runtime demo clock and its endpoints.
    #:
    #: Separate from DEMO_TIME_OFFSET_DAYS on purpose. That one is a static boot-time
    #: shift and `assert_production_safe` refuses to start with it set, because a
    #: forgotten offset in a real deployment corrupts overdue maths silently. This
    #: flag turns on a *runtime* clock that starts at zero, moves only through an
    #: audited endpoint, is visible in the UI whenever it is not zero, and can be
    #: wound back without a redeploy. A real multi-merchant deployment leaves it off.
    demo_controls_enabled: bool = False

    # --- Reviewer access -----------------------------------------------------
    #: Lets anyone reaching the login page open a READ-ONLY session without a
    #: credential being sent to them separately.
    #:
    #: The audit's complaint was practical rather than architectural: the public
    #: "Open the live demo" button led to a login wall with no way through, so a
    #: reviewer's first experience of a working system was a dead end. Handing out a
    #: shared password by email is worse — it is a real credential in a mailbox, and it
    #: is the same one for everyone.
    #:
    #: This grants a session on an existing account instead, and `auth.reviewer_login`
    #: refuses to issue one unless that account's role is `auditor`. The read-only
    #: guarantee is the role check that already exists in app.api.deps, not a promise
    #: made here — a misconfigured account name cannot quietly hand out write access.
    reviewer_access_enabled: bool = False
    #: Which account the reviewer button signs into. Must exist and must be an auditor.
    reviewer_username: str = "reviewer"

    # Phase 1 live identity is deployed dark until staging evidence and launch
    # ownership are complete. Demo authentication is independent of this flag.
    live_registration_enabled: bool = False

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
        if not self.session_secret or len(self.session_secret) < 32:
            raise RuntimeError(
                "SESSION_SECRET must be set to at least 32 random characters in production"
            )
        if self.razorpay_key_id.startswith("rzp_live_") and not self.allow_live_razorpay:
            raise RuntimeError("Live Razorpay credentials require ALLOW_LIVE_RAZORPAY=true")
        # Merchant Razorpay credentials are encrypted with this key. Without it the
        # code used to derive one from SESSION_SECRET, which meant rotating that
        # secret — the ordinary way to revoke every session — silently made every
        # stored credential undecryptable. Refuse to start rather than inherit it.
        if not self.credential_encryption_key:
            raise RuntimeError(
                "CREDENTIAL_ENCRYPTION_KEY must be set in production. Generate one with:\n"
                "  python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'"
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
        if not self.email_redirect_to and not self.allow_direct_customer_email:
            raise RuntimeError(
                "EMAIL_DRY_RUN is false but EMAIL_REDIRECT_TO is not set.\n"
                "Set EMAIL_REDIRECT_TO to your own address so reminders go to your "
                "inbox, leave EMAIL_DRY_RUN=true, or explicitly approve direct customer "
                "delivery with ALLOW_DIRECT_CUSTOMER_EMAIL=true."
            )
        if not self.resend_inbound_webhook_secret or self.email_reply_to_domain.casefold() in {
            "example.com",
            "example.invalid",
        }:
            raise RuntimeError(
                "Live email requires RESEND_INBOUND_WEBHOOK_SECRET and a configured "
                "EMAIL_REPLY_TO_DOMAIN so customer replies are authenticated and retained."
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
