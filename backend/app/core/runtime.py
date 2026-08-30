"""Runtime overrides that a reviewer can change without a redeploy.

Environment variables are the right home for deployment configuration, and the wrong
home for anything a person needs to change while looking at the product. These are the
few values a reviewer legitimately adjusts mid-session: where reminder mail goes, and
(via app.core.clock) what date the system believes it is.

Held in module state, mirrored to the `demo_settings` row, and rehydrated at startup by
app.services.demo_control. The cache exists because these are read on hot paths —
`resolve_recipient` runs on every send — and a database query per read would be both
slow and a layering violation, since app.core may not reach app.core.db.

**This makes the cache per-process.** The deployment runs a single uvicorn process
with the scheduler inside it, so the endpoint that sets an override, the cycle that
sends mail, and the webhook that accepts the reply all share one copy and cannot
disagree. Running multiple workers would break that: one worker would honour an
override the others had never heard of. If this ever needs more than one process,
these reads have to move to the database (they are rare enough to afford it) or to a
shared cache — module state is the wrong home the moment there is more than one.
"""

from app.core.config import settings

_email_redirect_override: str | None = None


def set_email_redirect_override(address: str | None) -> None:
    """Point reminder mail somewhere else. Called only by app.services.demo_control."""
    global _email_redirect_override
    cleaned = (address or "").strip()
    _email_redirect_override = cleaned or None


def email_redirect_override() -> str | None:
    return _email_redirect_override


def effective_email_redirect() -> str | None:
    """The address reminder mail is actually redirected to, if any.

    The runtime override wins over the environment default, but it can only ever
    *move* the redirect — never remove it. Clearing the override falls back to
    EMAIL_REDIRECT_TO rather than to "mail real customers", so no combination of
    runtime settings can turn a demo into an outbound campaign against the invented
    addresses in the seeded ledger.
    """
    return _email_redirect_override or settings.email_redirect_to
