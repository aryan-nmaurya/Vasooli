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
    #: Subscription billing runs on its own Razorpay credentials, separate from the
    #: platform key above.
    #:
    #: The platform key serves the DEMO — `razorpay_client_for_merchant` returns it
    #: for any demo merchant, and the dashboard shows a hard-coded "Test mode" badge
    #: next to it. Pointing that key at a live account would make the guided demo
    #: create real payment links for real money while still claiming to be in test
    #: mode. Subscription billing genuinely needs live credentials, so the two are
    #: split rather than forcing one to be wrong.
    #:
    #: Each falls back to the platform value when unset, so a deployment that has not
    #: split them yet behaves exactly as before.
    razorpay_billing_key_id: str | None = None
    razorpay_billing_key_secret: str | None = None
    razorpay_billing_webhook_secret: str | None = None
    razorpay_plan_id_starter: str | None = None
    razorpay_plan_id_growth: str | None = None
    razorpay_plan_id_scale: str | None = None
    razorpay_subscriptions_enabled: bool = False
    live_trial_days: int = Field(default=7, ge=1, le=90)
    #: Charged when the merchant authorises the Autopay mandate, then refunded once
    #: the subscription reports itself authenticated.
    #:
    #: A mandate cannot be validated for nothing: the payment is how the bank or UPI
    #: app confirms the customer genuinely approved recurring debits. Keeping it small
    #: and refunding it means the trial stays free in substance while the mandate is
    #: real, so the first post-trial charge does not fail on an unverified instrument.
    trial_auth_amount_paise: int = Field(default=200, ge=100, le=10_000)
    razorpay_oauth_client_id: str | None = None
    razorpay_oauth_client_secret: str | None = None
    razorpay_oauth_redirect_uri: str | None = None
    razorpay_oauth_token_url: str = "https://auth.razorpay.com/token"
    razorpay_oauth_scope: str = "read_write"
    razorpay_oauth_mode: Literal["test", "live"] = "test"
    zoho_oauth_client_id: str | None = None
    zoho_oauth_client_secret: str | None = None
    zoho_oauth_redirect_uri: str | None = None
    zoho_accounts_url: str = "https://accounts.zoho.com"
    zoho_oauth_scope: str = "ZohoBooks.invoices.READ,ZohoBooks.settings.READ"
    frontend_live_integrations_url: str = "http://localhost:3000/live/integrations"
    frontend_public_url: str = "http://localhost:3000"
    #: Minimum gap between Razorpay API calls. Test mode rate-limits aggressively —
    #: a 60-invoice batch fired flat out trips it within a few requests.
    razorpay_min_request_interval_seconds: float = 1.5
    razorpay_timeout_seconds: float = 10.0
    allow_live_razorpay: bool = False

    # --- Gemini via Google AI Studio ---
    # Model IDs are config, not literals: a retired or mistyped ID is a .env edit.
    google_api_key: str
    # Chosen from measurement, not the version number. Probed against the live API on
    # 2026-09-02: 3.7-flash never answered (0/4, hung to the timeout every time) and
    # 3.6-flash returned 504 DEADLINE_EXCEEDED through the client on every call, so
    # both cost a full `llm_timeout_seconds` before failing over. 3.5-flash answered
    # 4/4 in about a second. The newest model is not the fastest one here.
    gemini_primary_model: str = "gemini-3.5-flash"
    gemini_fallback_model: str = "gemini-3.6-flash"
    #: 20s was too tight for drafting: both Gemini models returned 504
    #: DEADLINE_EXCEEDED on real prompts often enough to keep the cycle's breaker
    #: tripped, while a trivial probe answered in about a second.
    llm_timeout_seconds: float = 45.0
    llm_max_retries: int = 2

    # --- Email ---
    resend_api_key: str
    sendgrid_api_key: str | None = None
    #: Resend gives every account this sender without any DNS setup. It can only
    #: deliver to the address the account was registered with, which is exactly the
    #: behaviour we want while the customer list is synthetic.
    email_from: str = "Vasooli <onboarding@resend.dev>"
    #: Account lifecycle mail has a stable platform identity and must never inherit a
    #: merchant's collections sender. The domain must be verified in Resend.
    #: Sender for verification and password-reset mail.
    #:
    #: Must be a domain verified in Resend, and it is NOT automatically the same as
    #: EMAIL_FROM. It defaulted to a `.com` that was never verified, so Resend
    #: refused every identity email with a 403 and no one could finish signing up or
    #: reset a password — while reminders, which use EMAIL_FROM, kept sending.
    auth_email_from: str = "Vasooli <noreply@vasooli.space>"
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
    # Explicit SaaS fallback: live merchants without a custom verified domain may
    # send through the platform's verified EMAIL_FROM identity.
    allow_platform_sender_for_live: bool = False
    global_send_kill_switch: bool = False
    global_daily_send_quota: int = 100000

    #: When set, every reminder is delivered here instead of to the customer, with the
    #: intended recipient shown in the subject. The synthetic ledger contains 52 fake
    #: domains, so nothing would arrive anyway — but the real reason is that this makes
    #: it impossible to email a live person by accident if a real address ever lands in
    #: the data. Required before live sending is allowed.
    email_redirect_to: str | None = None

    # --- Ops ---
    scheduler_enabled: bool = True
    # Alembic imports application settings while the one-shot migration container
    # runs. Treating that container as an invalid role makes every production deploy
    # fail before the first revision is applied.
    process_role: Literal["api", "scheduler", "worker", "migrate"] = "api"
    worker_kind: Literal["all", "recovery", "email", "erp", "billing"] = "all"
    worker_poll_seconds: int = Field(default=15, ge=1, le=300)
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

    #: Lets DEMO_CONTROLS_ENABLED survive `assert_production_safe`.
    #:
    #: The demo clock is process-global — `app.core.clock._runtime_offset_days` shifts
    #: `utcnow()` for every code path, not just the demo's. Advancing it on a
    #: deployment that holds real merchants would change how overdue their invoices
    #: are, when their trials end, and when their sessions expire. That is why
    #: production refuses it by default and why this override is separate rather than
    #: folded into the flag it unlocks: enabling the demo clock has to be one
    #: decision, and accepting that consequence has to be another.
    #:
    #: Defensible only while the live tenants are the operator's own and hold no
    #: invoices. Turn it off before onboarding anyone real; the guard below is what
    #: makes forgetting expensive rather than silent.
    allow_demo_controls_in_production: bool = False

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

    @property
    def effective_billing_key_id(self) -> str:
        """Billing credentials, falling back to the platform key when not split."""
        return self.razorpay_billing_key_id or self.razorpay_key_id

    @property
    def effective_billing_key_secret(self) -> str:
        return self.razorpay_billing_key_secret or self.razorpay_key_secret

    @property
    def effective_billing_webhook_secret(self) -> str:
        return self.razorpay_billing_webhook_secret or self.razorpay_webhook_secret

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
        # Demo affordances that have no business running in a production process.
        # `demo_time_offset_days` was already refused; these three were not, and each
        # is a way for the guided demo to reach a real merchant's data:
        #   * simulated replies write a fabricated customer statement into an audit
        #     trail, with no signature and no sender correlation;
        #   * demo controls move a clock the recovery cycle reads, which changes how
        #     overdue every live invoice is;
        #   * reviewer access is a shared credential to the operator console.
        # Scope checks now stop each of them crossing into live data on their own, but
        # a production process should not be offering them at all.
        # REVIEWER_ACCESS_ENABLED is deliberately NOT in this list.
        #
        # The other two write: a simulated reply fabricates a customer statement in
        # the audit trail, and demo controls move a clock the live recovery cycle
        # reads. Neither has any business running in production.
        #
        # Reviewer access only reads. The account must be an `auditor`, and
        # app.api.deps rejects every non-GET/HEAD/OPTIONS request from an auditor,
        # so the read-only property is enforced by the request path rather than
        # promised here. Demo/live isolation is separately enforced and covered by
        # tests. Forbidding it outright also forbade a public product demo, which is
        # a real need and not a security win.
        #
        # What this cannot check is whether the reviewer ACCOUNT is actually an
        # auditor — that needs a database. `verify_reviewer_account` does it at
        # startup, and the app refuses to serve if it is wrong.
        # ALLOW_SIMULATED_REPLIES has no override and should never get one: it writes
        # a fabricated customer statement into the audit trail with no signature and
        # no sender correlation, which is a lie about evidence rather than a shortcut
        # through time.
        if self.allow_simulated_replies:
            raise RuntimeError("ALLOW_SIMULATED_REPLIES must be false in production")
        if self.demo_controls_enabled and not self.allow_demo_controls_in_production:
            raise RuntimeError(
                "DEMO_CONTROLS_ENABLED must be false in production. The demo clock is "
                "process-global and would shift overdue counts, trial end dates and "
                "session expiry for every live merchant on this deployment. If this "
                "deployment holds no real merchants and you accept that, set "
                "ALLOW_DEMO_CONTROLS_IN_PRODUCTION=true as a separate, deliberate act."
            )
        if self.admin_api_key in {"", "changeme", "local-dev-key"}:
            raise RuntimeError("ADMIN_API_KEY must be set to a real secret in production")
        if not self.session_secret or len(self.session_secret) < 32:
            raise RuntimeError(
                "SESSION_SECRET must be set to at least 32 random characters in production"
            )
        if self.razorpay_key_id.startswith("rzp_live_") and not self.allow_live_razorpay:
            raise RuntimeError("Live Razorpay credentials require ALLOW_LIVE_RAZORPAY=true")
        if self.effective_billing_key_id.startswith("rzp_live_") and not self.allow_live_razorpay:
            raise RuntimeError("Live Razorpay billing credentials require ALLOW_LIVE_RAZORPAY=true")
        # The demo must never transact real money. It runs on the platform key, and
        # the dashboard states "Test mode" beside it as a fact, not a hope.
        if self.razorpay_key_id.startswith("rzp_live_"):
            raise RuntimeError(
                "RAZORPAY_KEY_ID is the DEMO credential and must stay in test mode. "
                "Put live subscription credentials in RAZORPAY_BILLING_KEY_ID instead."
            )
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
        if self.live_registration_enabled:
            if self.email_dry_run:
                raise RuntimeError(
                    "LIVE_REGISTRATION_ENABLED requires EMAIL_DRY_RUN=false so email "
                    "verification and password recovery can be completed"
                )
            if not self.frontend_public_url.startswith("https://"):
                raise RuntimeError(
                    "FRONTEND_PUBLIC_URL must be an https:// origin when live "
                    "registration is enabled"
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
