"""DNS-over-HTTPS sender-domain verification."""

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class DomainVerification:
    verified: bool
    records: list[str]


def verify_domain_dns(domain: str, token: str, *, timeout: float = 5) -> DomainVerification:
    """Verify the Vasooli TXT challenge without trusting a browser response.

    DNS-over-HTTPS keeps the API container free of resolver-specific dependencies;
    operators can still retry the check while SPF/DKIM/DMARC are provisioned by their
    email provider.
    """
    try:
        response = httpx.get(
            "https://cloudflare-dns.com/dns-query",
            params={"name": f"_vasooli.{domain}", "type": "TXT"},
            headers={"accept": "application/dns-json"},
            timeout=timeout,
        )
        if response.is_error:
            return DomainVerification(False, [])
        answers = response.json().get("Answer") or []
        records: list[str] = []
        for answer in answers:
            value: Any = answer.get("data")
            if isinstance(value, str):
                records.append(value.strip('"'))
        return DomainVerification(token in records, records)
    except httpx.HTTPError:
        return DomainVerification(False, [])
