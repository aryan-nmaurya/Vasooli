"""DNS-over-HTTPS sender-domain verification."""

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.integrations.email.resend_client import create_sending_domain, get_sending_domain


@dataclass(frozen=True)
class DomainVerification:
    verified: bool
    records: list[str]


@dataclass(frozen=True)
class ProviderDomain:
    provider_id: str
    status: str
    records: list[dict]


def provision_provider_domain(domain: str) -> ProviderDomain | None:
    """Register the merchant domain with Resend when delivery is configured."""
    if not settings.resend_api_key or settings.environment == "test":
        return None
    payload = create_sending_domain(domain)
    return ProviderDomain(
        provider_id=str(payload.get("id") or ""),
        status=str(payload.get("status") or "pending"),
        records=list(payload.get("records") or []),
    )


def provider_domain_status(provider_domain_id: str) -> ProviderDomain:
    payload = get_sending_domain(provider_domain_id)
    return ProviderDomain(
        provider_id=provider_domain_id,
        status=str(payload.get("status") or "pending"),
        records=list(payload.get("records") or []),
    )


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
