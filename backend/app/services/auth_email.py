"""Transactional identity email for the live account lifecycle.

These messages are intentionally separate from debt-reminder delivery. They go to the
account owner who requested them, do not use demo redirects, and do not create a
``Reminder`` row or consume a merchant's collections quota.
"""

from html import escape
from urllib.parse import quote

from app.core.config import settings
from app.integrations.email.base import EmailProvider
from app.integrations.email.resend_client import ResendProvider


class AuthEmailError(RuntimeError):
    """The identity message was not accepted by the configured provider."""


def send_auth_email(
    *,
    purpose: str,
    email: str,
    token: str,
    provider: EmailProvider | None = None,
) -> None:
    if purpose not in {"verify_email", "password_reset"}:
        raise ValueError("Unsupported identity email purpose")
    if settings.environment in {"local", "test"}:
        return
    if settings.email_dry_run:
        raise AuthEmailError("Identity email delivery is disabled")

    base = settings.frontend_public_url.rstrip("/")
    if purpose == "verify_email":
        subject = "Verify your Vasooli email"
        url = f"{base}/verify-email?token={quote(token, safe='')}"
        action = "Verify email"
        intro = "Verify this address to activate your Vasooli workspace."
    else:
        subject = "Reset your Vasooli password"
        url = f"{base}/reset-password?token={quote(token, safe='')}"
        action = "Reset password"
        intro = "A password reset was requested for your Vasooli account."

    text = f"{intro}\n\n{action}: {url}\n\nIf you did not request this, ignore this email."
    html = (
        "<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;"
        "font-size:15px;line-height:1.55;color:#1a1a1a;max-width:560px'>"
        f"<p>{escape(intro)}</p>"
        f"<p><a href='{escape(url, quote=True)}'>{escape(action)}</a></p>"
        "<p>If you did not request this, ignore this email.</p></div>"
    )
    result = (provider or ResendProvider()).send(
        to=email,
        subject=subject,
        html=html,
        text=text,
        idempotency_key=f"auth-{purpose}-{token[:24]}",
    )
    if not result.sent:
        raise AuthEmailError(result.error or "Identity email was not accepted")
