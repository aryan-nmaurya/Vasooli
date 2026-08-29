"""Email provider contract. Doc §10.

Kept behind a Protocol so the recovery cycle never learns which provider is in use,
and so the eval harness can mock delivery at this boundary rather than patching
somewhere deep inside a service.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SendResult:
    sent: bool
    provider: str
    message_id: str | None = None
    error: str | None = None
    #: True when the failure is worth retrying (timeout, 5xx, rate limit) rather than
    #: permanent (rejected address, bad key).
    retryable: bool = False


class EmailProvider(Protocol):
    name: str

    def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
        reply_to: str | None = None,
        headers: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> SendResult: ...
