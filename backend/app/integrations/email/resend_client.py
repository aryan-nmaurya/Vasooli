"""Resend delivery. Doc §10.

Called through app.services.messaging, which owns the redirect guard — this module
sends exactly where it is told and does not decide policy about recipients.
"""

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.email.base import SendResult

log = get_logger("email.resend")

API_URL = "https://api.resend.com/emails"


class ResendProvider:
    name = "resend"

    def __init__(self, api_key: str | None = None, timeout: float = 20.0) -> None:
        self._api_key = api_key or settings.resend_api_key
        self._timeout = timeout

    def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
        reply_to: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> SendResult:
        payload: dict[str, object] = {
            "from": settings.email_from,
            "to": [to],
            "subject": subject,
            "html": html,
            "text": text,
        }
        if reply_to:
            payload["reply_to"] = reply_to
        if headers:
            payload["headers"] = headers

        try:
            response = httpx.post(
                API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            return SendResult(
                sent=False,
                provider=self.name,
                error=f"{type(exc).__name__}: {exc}",
                retryable=True,
            )

        if response.status_code < 300:
            message_id = response.json().get("id")
            log.info("email.sent", to=to, message_id=message_id, subject=subject)
            return SendResult(sent=True, provider=self.name, message_id=message_id)

        # 429 and 5xx clear on their own; 4xx will not, and retrying just burns quota.
        retryable = response.status_code == 429 or response.status_code >= 500
        error = response.text[:300]
        log.warning(
            "email.send_failed",
            to=to,
            status=response.status_code,
            error=error,
            retryable=retryable,
        )
        return SendResult(
            sent=False,
            provider=self.name,
            error=f"{response.status_code}: {error}",
            retryable=retryable,
        )
