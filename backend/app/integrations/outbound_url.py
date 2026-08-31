"""Refuse to fetch a merchant-supplied URL that points back inside the network.

The Tally integration is configured by the merchant: they run the edge agent, so they
give Vasooli its address. Nothing validated that address, and the adapter fetched it
with `httpx.get` and parsed the response into the ledger. An authenticated merchant on
a free onboarding workspace could therefore point a connection at
`http://169.254.169.254/latest/meta-data/…` and have the production API request it —
on demand, and again every half hour from the scheduler, which does not even check
billing entitlement first. On EC2 that address hands out IAM credentials.

That is a server-side request forgery with a read-back channel: the response body is
parsed as an invoice feed, so what comes back reaches the merchant through sync runs
and ingestion errors.

The checks here are the ordinary ones and they are all necessary:

* **Scheme.** `https` only. An agent exposing a plaintext feed across the internet is
  already leaking its own ledger; `http` is permitted only against loopback outside
  production, where the developer fixtures live.
* **Address, not hostname.** A name is resolved and *every* address it returns is
  checked. Blocking the literal string `169.254.169.254` stops nothing — a hostname
  resolving to it is the normal form of this attack.
* **Every reserved range**, not just RFC 1918: loopback, link-local (which is where
  the cloud metadata services sit), carrier-grade NAT, multicast, and reserved.
* **No credentials or fragments** in the URL, which are ways to make a destination
  read as something it is not.

Redirects are handled at the call site: `httpx` does not follow them unless asked, and
the adapters do not ask. A followed redirect would move the destination after this
check had passed.

This narrows the check to a moment in time — DNS can change between validation and
fetch — so it is applied at *both* points: when the connection is saved, so the
merchant gets an immediate and specific error, and again before each fetch, so a later
DNS change does not turn a stored connection into a live SSRF.
"""

import ipaddress
import socket
from urllib.parse import urlsplit

from app.core.config import settings


class UnsafeOutboundURLError(ValueError):
    """The URL is syntactically fine and must still not be requested."""


def _blocked(address: str) -> str | None:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return "address could not be parsed"
    if ip.is_loopback:
        return "loopback address"
    if ip.is_link_local:
        # 169.254.0.0/16 and fe80::/10 — the cloud instance metadata services.
        return "link-local address"
    if ip.is_private:
        return "private address"
    if ip.is_multicast:
        return "multicast address"
    if ip.is_reserved or ip.is_unspecified:
        return "reserved address"
    # 100.64.0.0/10, carrier-grade NAT: routable-looking, not the public internet.
    if ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"):
        return "shared address space"
    return None


def _resolve(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except socket.gaierror as exc:
        raise UnsafeOutboundURLError(f"host {host!r} could not be resolved") from exc


def assert_safe_outbound_url(raw: str, *, what: str = "endpoint") -> str:
    """Return `raw` unchanged, or raise `UnsafeOutboundURLError` explaining the refusal.

    The message names the reason without echoing the resolved address back, so this
    does not become a convenient internal port scanner with a nicer interface.
    """
    parts = urlsplit(raw.strip())
    if parts.scheme not in {"http", "https"}:
        raise UnsafeOutboundURLError(f"{what} must be an http(s) URL")
    if parts.username or parts.password:
        raise UnsafeOutboundURLError(f"{what} must not embed credentials")
    if parts.fragment:
        raise UnsafeOutboundURLError(f"{what} must not contain a fragment")

    host = parts.hostname
    if not host:
        raise UnsafeOutboundURLError(f"{what} has no host")

    addresses = _resolve(host)
    reasons = {reason for a in addresses if (reason := _blocked(a))}

    if parts.scheme == "http":
        # A loopback fixture is how the adapters are exercised in development. Never in
        # production, and never for anything that is not loopback.
        loopback_only = addresses and all(ipaddress.ip_address(a).is_loopback for a in addresses)
        if settings.is_production or not loopback_only:
            raise UnsafeOutboundURLError(f"{what} must use https")
        return raw

    if reasons:
        raise UnsafeOutboundURLError(
            f"{what} resolves to an internal address ({sorted(reasons)[0]}); "
            "it must be reachable on the public internet"
        )
    return raw
