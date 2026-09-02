"""A merchant-supplied integration endpoint must not reach inside the network.

The Tally adapter fetched whatever URL the merchant stored and parsed the response
into their ledger. Pointed at `http://169.254.169.254/…` on EC2, that is the instance
metadata service handing out IAM credentials — requested by the production API, on
demand, and again from the scheduler every half hour.
"""

import socket

import pytest

from app.core.config import settings
from app.integrations.outbound_url import UnsafeOutboundURLError, assert_safe_outbound_url

#: A name that resolves to the cloud metadata address. Resolution is stubbed so the
#: test does not depend on the machine's DNS, which is exactly what the check resolves.
REBIND_HOST = "agent.merchant.example"


@pytest.fixture
def resolves(monkeypatch):
    def _install(mapping: dict[str, list[str]]):
        def fake_getaddrinfo(host, *_args, **_kwargs):
            if host not in mapping:
                raise socket.gaierror(f"unknown host {host}")
            return [(None, None, None, "", (address, 0)) for address in mapping[host]]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    return _install


@pytest.mark.parametrize(
    ("address", "why"),
    [
        ("169.254.169.254", "AWS/GCP/Azure instance metadata"),
        ("127.0.0.1", "loopback: the API's own private routes"),
        ("10.4.1.9", "RFC 1918: anything else in the VPC"),
        ("192.168.1.1", "the household router of a self-hosted deployment"),
        ("172.17.0.2", "the Postgres container on the docker bridge"),
        ("100.64.0.7", "carrier-grade NAT, which looks routable and is not"),
        ("0.0.0.0", "unspecified"),
        ("fe80::1", "IPv6 link-local, the same metadata trick"),
        ("::1", "IPv6 loopback"),
    ],
)
def test_a_hostname_resolving_inside_the_network_is_refused(resolves, address, why):
    """Checking the address, not the string, is the whole point.

    Blocking the literal `169.254.169.254` stops nobody: a merchant controls their own
    DNS and points an ordinary-looking hostname at it.
    """
    resolves({REBIND_HOST: [address]})
    with pytest.raises(UnsafeOutboundURLError, match="internal address"):
        assert_safe_outbound_url(f"https://{REBIND_HOST}/feed", what="Tally agent endpoint")


def test_one_bad_address_among_several_is_enough_to_refuse(resolves):
    """A name that returns both a public and an internal address is still an attack."""
    resolves({REBIND_HOST: ["93.184.216.34", "169.254.169.254"]})
    with pytest.raises(UnsafeOutboundURLError):
        assert_safe_outbound_url(f"https://{REBIND_HOST}/feed")


def test_a_public_https_endpoint_is_allowed(resolves):
    resolves({REBIND_HOST: ["93.184.216.34"]})
    url = f"https://{REBIND_HOST}/v1/invoices"
    assert assert_safe_outbound_url(url) == url


def test_plaintext_is_refused_even_to_a_public_host(resolves):
    """An agent feed over http leaks the merchant's own ledger in transit."""
    resolves({REBIND_HOST: ["93.184.216.34"]})
    with pytest.raises(UnsafeOutboundURLError, match="https"):
        assert_safe_outbound_url(f"http://{REBIND_HOST}/v1/invoices")


def test_loopback_over_http_is_allowed_outside_production_only(resolves, monkeypatch):
    """Developer fixtures run on loopback; production has no such fixtures."""
    resolves({"localhost": ["127.0.0.1"]})
    assert assert_safe_outbound_url("http://localhost:9911") == "http://localhost:9911"

    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(UnsafeOutboundURLError):
        assert_safe_outbound_url("http://localhost:9911")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://internal:70/",
        "ftp://internal/",
        "not-a-url",
    ],
)
def test_non_http_schemes_are_refused(url):
    with pytest.raises(UnsafeOutboundURLError, match="http"):
        assert_safe_outbound_url(url)


def test_embedded_credentials_are_refused(resolves):
    """`https://public.example@169.254.169.254/` reads as one host and reaches another."""
    resolves({REBIND_HOST: ["93.184.216.34"]})
    with pytest.raises(UnsafeOutboundURLError, match="credentials"):
        assert_safe_outbound_url(f"https://user:secret@{REBIND_HOST}/feed")


def test_an_unresolvable_host_fails_closed(resolves):
    resolves({})
    with pytest.raises(UnsafeOutboundURLError, match="could not be resolved"):
        assert_safe_outbound_url("https://nowhere.invalid/feed")


def test_the_zoho_adapter_validates_at_construction(resolves):
    """Re-checked before every fetch, not only when the connection was saved.

    A stored connection is validated once; DNS can be repointed afterwards, and the
    scheduler re-fetches it every half hour. Zoho returns its API domain during OAuth,
    so that value is merchant-influenced and this process would request it with its
    own network position.
    """
    from app.integrations.erp import ZohoBooksAdapter

    resolves({REBIND_HOST: ["169.254.169.254"]})
    with pytest.raises(UnsafeOutboundURLError):
        ZohoBooksAdapter(
            {
                "access_token": "tok",
                "organization_id": "org",
                "api_domain": f"https://{REBIND_HOST}",
            }
        )
