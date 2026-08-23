"""Session tokens and credential checks.

Two ways to prove you are the operator:

* `X-Admin-Key` — a long-lived shared secret, for scripts, the scheduler, and the
  dashboard's server-side proxy. Never reaches a browser.
* A signed session token — issued after a password login, carried in an httpOnly
  cookie, and expiring on its own.

Both grant the same single operator role. Vasooli runs for one merchant; per-user
accounts and an IAM model would be scaffolding around a system that has exactly one
user, and scaffolding nobody needs is where security bugs hide.

The token is a hand-rolled HMAC rather than a JWT: it carries an expiry and a subject
and nothing else, so a library that also supports thirty algorithms — including `none`
— is a larger attack surface than the problem justifies.
"""

import base64
import hashlib
import hmac
import secrets
import time

from app.core.config import settings

#: Bumped if the token format ever changes, so old tokens fail closed rather than
#: being misparsed.
TOKEN_VERSION = "v1"

SESSION_COOKIE = "vasooli_session"
DEFAULT_TTL_SECONDS = 12 * 60 * 60  # one working day


def _sign(payload: str) -> str:
    digest = hmac.new(
        settings.session_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_session_token(subject: str = "operator", ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Mint a signed, expiring session token."""
    expires_at = int(time.time()) + ttl_seconds
    payload = f"{TOKEN_VERSION}.{subject}.{expires_at}"
    return f"{payload}.{_sign(payload)}"


def verify_session_token(token: str | None) -> str | None:
    """Return the subject if the token is valid and unexpired, else None.

    Fails closed on every malformed input rather than raising: a caller that forgets
    to handle an exception should end up unauthenticated, not with a 500.
    """
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 4:
        return None

    version, subject, expires_raw, signature = parts
    if version != TOKEN_VERSION:
        return None

    payload = f"{version}.{subject}.{expires_raw}"
    # compare_digest, not ==: a plain comparison exits at the first differing byte and
    # leaks the expected signature through response timing.
    if not hmac.compare_digest(_sign(payload), signature):
        return None

    try:
        expires_at = int(expires_raw)
    except ValueError:
        return None
    if expires_at < time.time():
        return None

    return subject


def check_admin_key(candidate: str | None) -> bool:
    if not candidate:
        return False
    return secrets.compare_digest(candidate, settings.admin_api_key)


def check_dashboard_password(candidate: str | None) -> bool:
    if not candidate or not settings.dashboard_password:
        return False
    return secrets.compare_digest(candidate, settings.dashboard_password)
