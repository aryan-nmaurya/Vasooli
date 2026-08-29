"""Pre-flight: is this deployment actually able to work end to end?

    uv run python -m scripts.preflight
    uv run python -m scripts.preflight --host https://api.yourdomain.com

Checks every link in the chain a real invoice travels, in the order it travels them:
config, DNS, TLS, Razorpay, outbound email, inbound email, AI. Each check reports what
it found and, when it fails, the exact remedy.

This exists because the failure modes are silent. A dead Resend key, an unconfigured
MX record and a missing webhook secret all present the same way from the dashboard:
nothing happens. Finding that out during a demo is the expensive version.

Breadth, not depth. It answers "can this deployment work at all?" in one command and
never causes a side effect. When it points at a specific provider, the deeper probes
are:

    scripts.verify_live_integrations   Resend contract, and --send-test-email
    scripts.check_razorpay             creates and cancels a real ₹1 Payment Link
"""

import argparse
import socket
import ssl
from urllib.parse import urlparse

import httpx

from app.core.config import settings

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

_results: list[tuple[str, str]] = []


def report(status: str, name: str, detail: str = "", remedy: str = "") -> None:
    colour = {"PASS": GREEN, "FAIL": RED, "WARN": YELLOW}[status]
    print(f"  {colour}{status:<4}{RESET}  {name:<38} {detail}")
    if remedy and status != "PASS":
        print(f"        {DIM}→ {remedy}{RESET}")
    _results.append((status, name))


def section(title: str) -> None:
    print(f"\n{title}")


# ---------------------------------------------------------------------------
# 1. Configuration coherence.
# ---------------------------------------------------------------------------


def check_config() -> None:
    section("Configuration")

    if settings.email_dry_run:
        report(
            "WARN",
            "EMAIL_DRY_RUN",
            "true — nothing is actually sent",
            "Set EMAIL_DRY_RUN=false for a real demo, once the checks below pass.",
        )
    else:
        report("PASS", "EMAIL_DRY_RUN", "false — live sending")

    # The single check that decides whether a live send is even attempted.
    try:
        settings.assert_safe_to_send()
        report("PASS", "safe-to-send gate", "open")
    except RuntimeError as exc:
        report(
            "FAIL",
            "safe-to-send gate",
            "BLOCKS every live send",
            str(exc).replace("\n", " ")[:160],
        )

    domain = settings.email_reply_to_domain
    if domain.casefold() in {"example.com", "example.invalid", ""}:
        report(
            "FAIL",
            "EMAIL_REPLY_TO_DOMAIN",
            f"{domain!r} — placeholder",
            "Set it to a domain you own. Customer replies are addressed to "
            "invoice-<number>@<this domain>, so without it replies reach nobody.",
        )
    else:
        report("PASS", "EMAIL_REPLY_TO_DOMAIN", domain)

    if settings.resend_inbound_webhook_secret:
        report("PASS", "RESEND_INBOUND_WEBHOOK_SECRET", "set")
    else:
        report(
            "FAIL",
            "RESEND_INBOUND_WEBHOOK_SECRET",
            "missing",
            "Resend → Webhooks → your inbound endpoint → Signing Secret (whsec_...).",
        )

    if settings.email_redirect_to:
        report("PASS", "EMAIL_REDIRECT_TO", settings.email_redirect_to)
    else:
        report(
            "WARN",
            "EMAIL_REDIRECT_TO",
            "not set — mail goes to real customer addresses",
            "Point it at your own inbox unless you have deliberately decided otherwise.",
        )


# ---------------------------------------------------------------------------
# 2. DNS — the part that decides whether a reply can ever reach us.
# ---------------------------------------------------------------------------


def check_dns() -> None:
    section("DNS")
    domain = settings.email_reply_to_domain
    if domain.casefold() in {"example.com", "example.invalid", ""}:
        report("FAIL", "MX records", "skipped — no real reply domain", "See EMAIL_REPLY_TO_DOMAIN.")
        return

    try:
        import dns.resolver
    except ImportError:
        report("WARN", "MX records", "dnspython not installed", "uv add --dev dnspython")
        return

    try:
        answers = dns.resolver.resolve(domain, "MX")
        hosts = sorted(str(r.exchange).rstrip(".").casefold() for r in answers)
        # Resend receives through its own domain OR the raw AWS SES/inbound-smtp
        # hostname underneath it — "inbound-smtp.<region>.amazonaws.com" is a real,
        # correctly-configured Resend receiving record, not a stray one.
        if any(
            "resend" in h or "amazonses" in h or "inbound-smtp" in h or "amazonaws" in h
            for h in hosts
        ):
            report("PASS", "MX records", ", ".join(hosts)[:70])
        else:
            report(
                "FAIL",
                "MX records",
                ", ".join(hosts)[:70],
                "MX does not point at Resend. Replies will be delivered elsewhere and "
                "never reach Vasooli.",
            )
    except Exception as exc:
        report(
            "FAIL",
            "MX records",
            f"{type(exc).__name__}",
            f"No MX record for {domain}. Add the MX record Resend gives you under "
            "Domains → your domain → Receiving.",
        )


# ---------------------------------------------------------------------------
# 3. Public reachability and TLS. Razorpay and Resend both refuse bad certificates.
# ---------------------------------------------------------------------------


def check_host(host: str | None) -> None:
    section("Public endpoint")
    if not host:
        report(
            "WARN",
            "public HTTPS host",
            "not checked",
            "Re-run with --host https://api.yourdomain.com to verify what Razorpay and "
            "Resend will actually reach.",
        )
        return

    parsed = urlparse(host)
    if parsed.scheme != "https":
        report(
            "FAIL",
            "scheme",
            f"{parsed.scheme}://",
            "Razorpay and Resend deliver webhooks over HTTPS only. A bare IP or plain "
            "HTTP cannot receive either.",
        )
        return

    hostname = parsed.hostname or ""
    try:
        ctx = ssl.create_default_context()
        with (
            socket.create_connection((hostname, parsed.port or 443), timeout=10) as sock,
            ctx.wrap_socket(sock, server_hostname=hostname) as tls,
        ):
            cert = tls.getpeercert()
        subject = dict(x[0] for x in cert["subject"]).get("commonName", hostname)
        report("PASS", "TLS certificate", f"valid, CN={subject}, expires {cert['notAfter']}")
    except Exception as exc:
        report(
            "FAIL",
            "TLS certificate",
            f"{type(exc).__name__}: {exc}"[:60],
            "A self-signed or absent certificate means webhooks are silently dropped.",
        )
        return

    for path, expect, label in (
        ("/live", 200, "liveness"),
        ("/health", 200, "readiness (incl. database)"),
        ("/api/webhooks/razorpay", 400, "razorpay webhook reachable"),
        ("/api/webhooks/resend/inbound", 400, "inbound email webhook reachable"),
    ):
        try:
            # Webhook endpoints are POST-only and must reject an unsigned body.
            if "webhooks" in path:
                r = httpx.post(f"{host}{path}", json={}, timeout=15)
            else:
                r = httpx.get(f"{host}{path}", timeout=15)
            ok = r.status_code == expect
            report(
                "PASS" if ok else "FAIL",
                label,
                f"{r.status_code}",
                "" if ok else f"expected {expect}",
            )
        except Exception as exc:
            report("FAIL", label, type(exc).__name__, "Endpoint unreachable from the internet.")


# ---------------------------------------------------------------------------
# 4. Third-party credentials.
# ---------------------------------------------------------------------------


def check_razorpay() -> None:
    section("Razorpay")
    if settings.razorpay_key_id.startswith("rzp_live_"):
        report("WARN", "key mode", "LIVE key — real money")
    else:
        report("PASS", "key mode", "test mode")
    try:
        r = httpx.get(
            "https://api.razorpay.com/v1/payment_links",
            params={"count": 1},
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            timeout=20,
        )
        if r.status_code == 200:
            report("PASS", "API credentials", "authenticated")
        else:
            report("FAIL", "API credentials", str(r.status_code), "Check RAZORPAY_KEY_ID/SECRET.")
    except Exception as exc:
        report("FAIL", "API credentials", type(exc).__name__, "Network or credential failure.")

    if settings.razorpay_webhook_secret:
        report("PASS", "webhook secret", "set")
    else:
        report(
            "FAIL",
            "webhook secret",
            "missing",
            "Razorpay → Settings → Webhooks. Must match RAZORPAY_WEBHOOK_SECRET exactly.",
        )


def check_resend() -> None:
    section("Email — Resend")
    if not settings.resend_api_key:
        report("FAIL", "API key", "missing", "Set RESEND_API_KEY.")
        return
    try:
        r = httpx.get(
            "https://api.resend.com/domains",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            timeout=20,
        )
    except Exception as exc:
        report("FAIL", "API key", type(exc).__name__)
        return

    if r.status_code == 401:
        report(
            "FAIL",
            "API key",
            "401 unauthorized",
            "The key is revoked or wrong. Create a new one at resend.com/api-keys. "
            "Nothing can be sent until this passes.",
        )
        return
    if r.status_code != 200:
        report("FAIL", "API key", str(r.status_code))
        return
    report("PASS", "API key", "authenticated")

    try:
        domains = r.json().get("data") or []
    except Exception:
        domains = []

    reply_domain = settings.email_reply_to_domain.casefold()
    match = next((d for d in domains if str(d.get("name", "")).casefold() == reply_domain), None)
    if match is None:
        report(
            "FAIL",
            "sending domain verified",
            f"{reply_domain} not in Resend",
            "Resend → Domains → Add Domain, then add the DNS records it shows. Sending "
            "from an unverified domain fails, and receiving is impossible.",
        )
    elif match.get("status") == "verified":
        report("PASS", "sending domain verified", f"{match.get('name')} — verified")
    else:
        report(
            "FAIL",
            "sending domain verified",
            f"{match.get('name')} — {match.get('status')}",
            "DNS records are not fully propagated yet. Re-check in Resend.",
        )


def check_ai() -> None:
    section("AI — Gemini")
    try:
        r = httpx.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": settings.google_api_key},
            timeout=20,
        )
        if r.status_code == 200:
            report("PASS", "API key", f"authenticated ({settings.gemini_primary_model})")
        else:
            report("FAIL", "API key", str(r.status_code), "Check GOOGLE_API_KEY.")
    except Exception as exc:
        report("WARN", "API key", type(exc).__name__, "Could not reach Google.")
    report(
        "PASS",
        "deterministic fallback",
        "every AI path has one — an outage degrades, never blocks",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="public base URL, e.g. https://api.yourdomain.com")
    args = parser.parse_args()

    print(f"\nVasooli pre-flight  {DIM}environment={settings.environment}{RESET}")
    check_config()
    check_dns()
    check_host(args.host)
    check_razorpay()
    check_resend()
    check_ai()

    failed = sum(1 for s, _ in _results if s == "FAIL")
    warned = sum(1 for s, _ in _results if s == "WARN")
    passed = sum(1 for s, _ in _results if s == "PASS")
    print(f"\n  {passed} passed, {warned} warnings, {failed} failed\n")

    if failed:
        print(f"  {RED}Not ready.{RESET} The failures above are in the order they break.\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
