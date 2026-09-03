"""Typed wrapper around the Razorpay Payment Links API. Doc §4.

Thin on purpose. Retry and error translation live here; every decision about *when* to
call Razorpay lives in app.services.provisioning. That split is what lets the eval
harness swap this whole module for a fake and still exercise production code paths.
"""

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import razorpay
import requests
from razorpay.errors import BadRequestError, GatewayError, ServerError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("razorpay")


class RazorpayError(Exception):
    """Base for everything this module raises."""


class RazorpayPermanentError(RazorpayError):
    """A request Razorpay will refuse the same way every time.

    Bad payload, disabled product, invalid amount. Retrying only burns rate limit, so
    the caller should record the failure and move on.
    """


class RazorpayTransientError(RazorpayError):
    """A timeout, a 5xx, or a rate limit. Worth retrying."""


class RazorpayDuplicateReferenceError(RazorpayPermanentError):
    """A payment link with this reference_id already exists upstream."""


#: Razorpay's SDK dispatches on `error.code`, not HTTP status, and a 429 arrives
#: labelled BAD_REQUEST_ERROR — indistinguishable from a genuinely malformed request
#: unless the description is read. Treating rate limits as permanent means a batch
#: gives up on the first burst instead of backing off, which is exactly what happened
#: the first time this ran against 60 invoices.
_RATE_LIMIT_MARKERS = ("too many requests", "rate limit")

#: Razorpay rejects a reference_id it has already seen. That is the duplicate guard
#: working as intended, but it also means a link can exist upstream while our row is
#: gone — after a database reset, for instance. Recognised so the caller can adopt
#: the existing link instead of failing.
_DUPLICATE_REFERENCE_MARKER = "reference_id"


@dataclass(frozen=True)
class PaymentLinkResult:
    """The fields provisioning actually needs, lifted out of Razorpay's payload."""

    id: str
    short_url: str
    reference_id: str
    status: str
    amount_paise: int
    amount_paid_paise: int
    raw: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PaymentLinkResult":
        return cls(
            id=payload["id"],
            short_url=payload["short_url"],
            reference_id=payload.get("reference_id") or "",
            status=payload.get("status", "created"),
            amount_paise=int(payload.get("amount", 0)),
            amount_paid_paise=int(payload.get("amount_paid", 0) or 0),
            raw=payload,
        )


_RETRY = dict(
    retry=retry_if_exception_type(RazorpayTransientError),
    # Five attempts with a long ceiling: a tripped rate limit needs tens of seconds to
    # clear, not the two or three a short backoff would give it.
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)


class _TimeoutSession(requests.Session):
    """Supply a timeout even though Razorpay's SDK omits one."""

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self._timeout)
        return super().request(method, url, **kwargs)


class RazorpayClient:
    """Paced, retrying wrapper.

    Pacing lives here rather than in the batch loop so every caller is throttled —
    including the dashboard's retry button and the scheduler — not just the one path
    someone remembered to slow down.
    """

    def __init__(self, key_id: str | None = None, key_secret: str | None = None) -> None:
        self._api_key_id = key_id or settings.razorpay_key_id
        self._api_key_secret = key_secret or settings.razorpay_key_secret
        self._client = razorpay.Client(
            session=_TimeoutSession(settings.razorpay_timeout_seconds),
            auth=(self._api_key_id, self._api_key_secret),
        )
        self._min_interval = settings.razorpay_min_request_interval_seconds
        self._last_call_at = 0.0
        self._pace_lock = threading.Lock()

    def _pace(self) -> None:
        """Hold requests to at most one per configured interval."""
        if self._min_interval <= 0:
            return
        with self._pace_lock:
            gap = time.monotonic() - self._last_call_at
            if gap < self._min_interval:
                time.sleep(self._min_interval - gap)
            self._last_call_at = time.monotonic()

    # ------------------------------------------------------------------
    # Error translation. Razorpay's SDK raises BadRequestError for both
    # "your payload is wrong" and "this product is not enabled", so the
    # distinction that matters to us is 4xx vs 5xx, not the exception class.
    # ------------------------------------------------------------------

    def _is_configured(self) -> bool:
        """Real credentials, or a placeholder?

        Mirrors app.ai.client. Without this check a test run or a half-configured
        deploy makes real HTTP calls with junk credentials, sits through the
        transient-retry backoff, and takes thirty seconds to learn what is knowable
        immediately — and quietly makes the test suite depend on the network.
        """
        return bool(self._api_key_id) and "PLACEHOLDER" not in self._api_key_id

    def _call(self, fn, *args, **kwargs):
        if not self._is_configured():
            # Permanent, not transient: retrying a placeholder key cannot help.
            raise RazorpayPermanentError(
                "Razorpay is not configured (RAZORPAY_KEY_ID is a placeholder)"
            )
        self._pace()
        try:
            return fn(*args, **kwargs)
        except BadRequestError as exc:
            message = str(exc)
            lowered = message.lower()
            if any(m in lowered for m in _RATE_LIMIT_MARKERS):
                raise RazorpayTransientError(message) from exc
            if _DUPLICATE_REFERENCE_MARKER in lowered and "already exists" in lowered:
                raise RazorpayDuplicateReferenceError(message) from exc
            # A genuine 4xx. Never retried: the same request will fail identically.
            raise RazorpayPermanentError(message) from exc
        except (GatewayError, ServerError) as exc:
            raise RazorpayTransientError(str(exc)) from exc
        except Exception as exc:  # network/timeouts surface as bare exceptions
            raise RazorpayTransientError(f"{type(exc).__name__}: {exc}") from exc

    @retry(**_RETRY)
    def create_payment_link(
        self,
        *,
        amount_paise: int,
        reference_id: str,
        description: str,
        customer_name: str,
        customer_email: str,
        customer_phone: str | None,
        notes: dict[str, str],
        accept_partial: bool = True,
        expire_by: datetime | None = None,
    ) -> PaymentLinkResult:
        """Create one payment link for one invoice.

        `reference_id` and `notes` both come back untouched in the webhook payload,
        giving reconciliation two independent ways to identify the invoice. Neither
        involves matching on amount, which would be ambiguous the moment two customers
        owe the same round number.
        """
        payload: dict[str, Any] = {
            "amount": amount_paise,
            "currency": "INR",
            "accept_partial": accept_partial,
            "reference_id": reference_id,
            "description": description[:255],
            "customer": {"name": customer_name, "email": customer_email},
            # Vasooli sends its own reminders through Resend, with its own tone and
            # cadence. Letting Razorpay notify as well would double-contact the
            # customer and break the cadence caps the policy engine enforces.
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "notes": notes,
        }
        if customer_phone:
            payload["customer"]["contact"] = customer_phone
        if accept_partial:
            # Razorpay requires a floor when partial payment is allowed. ₹1 keeps it
            # effectively open — any real payment clears it.
            payload["first_min_partial_amount"] = 100
        if expire_by is not None:
            payload["expire_by"] = int(expire_by.timestamp())

        result = PaymentLinkResult.from_payload(
            self._call(self._client.payment_link.create, payload)
        )
        log.info(
            "razorpay.payment_link_created",
            link_id=result.id,
            reference_id=reference_id,
            amount_paise=amount_paise,
        )
        return result

    @retry(**_RETRY)
    def fetch_payment_link(self, link_id: str) -> PaymentLinkResult:
        return PaymentLinkResult.from_payload(self._call(self._client.payment_link.fetch, link_id))

    @retry(**_RETRY)
    def find_by_reference_id(self, reference_id: str) -> PaymentLinkResult | None:
        """Look up a link Razorpay already holds for this reference.

        Used to recover from a duplicate-reference rejection: the link exists, we just
        lost our record of it, and re-creating under a fresh reference would leave the
        customer two ways to pay one invoice.
        """
        payload = self._call(self._client.payment_link.all, {"reference_id": reference_id})
        items = payload.get("payment_links") or []
        return PaymentLinkResult.from_payload(items[0]) if items else None

    @retry(**_RETRY)
    def cancel_payment_link(self, link_id: str) -> PaymentLinkResult:
        """Close a link once the invoice is recovered or written off.

        Razorpay refuses to cancel an already-paid link; that is reported as a
        permanent error and the caller treats it as a no-op.
        """
        result = PaymentLinkResult.from_payload(
            self._call(self._client.payment_link.cancel, link_id)
        )
        log.info("razorpay.payment_link_cancelled", link_id=link_id)
        return result

    @retry(**_RETRY)
    def create_subscription(
        self,
        *,
        plan_id: str,
        total_count: int = 12,
        customer_notify: bool = True,
        start_at: int | None = None,
        auth_amount_paise: int | None = None,
    ) -> dict[str, Any]:
        """Create a subscription, optionally after a trial and behind a mandate check.

        `start_at` is when the first real billing cycle begins. Setting it in the
        future is what makes the trial free: Razorpay does not charge the plan amount
        until then.

        `auth_amount_paise` adds a one-time line item that the customer pays when
        they authorise the mandate. A mandate cannot be validated for nothing — the
        payment is how the bank or UPI app confirms the customer really approved
        recurring debits — so this small amount is charged now and refunded once the
        subscription reports itself authenticated.
        """
        payload: dict[str, Any] = {
            "plan_id": plan_id,
            "total_count": total_count,
            "customer_notify": 1 if customer_notify else 0,
        }
        if start_at is not None:
            payload["start_at"] = start_at
        if auth_amount_paise:
            payload["addons"] = [
                {
                    "item": {
                        "name": "Mandate verification",
                        "amount": auth_amount_paise,
                        "currency": "INR",
                    }
                }
            ]
        return self._call(self._client.subscription.create, payload)

    @retry(**_RETRY)
    def refund_payment(
        self, payment_id: str, *, amount_paise: int | None = None, notes: dict | None = None
    ) -> dict[str, Any]:
        """Refund a payment, in full unless an amount is given.

        Used to return the mandate-verification charge. Idempotent at the provider by
        `speed`/amount semantics only, so the caller records the refund locally and
        does not re-issue one it has already seen.
        """
        data: dict[str, Any] = {}
        if amount_paise is not None:
            data["amount"] = amount_paise
        if notes:
            data["notes"] = notes
        return self._call(self._client.payment.refund, payment_id, data)

    @retry(**_RETRY)
    def fetch_subscription(self, subscription_id: str) -> dict[str, Any]:
        """Read provider state for the daily billing reconciliation job."""
        return self._call(self._client.subscription.fetch, subscription_id)

    @retry(**_RETRY)
    def find_auth_payment_id(self, subscription_id: str) -> str | None:
        """The payment that authorised a subscription's mandate, or None.

        Razorpay does NOT put a payment entity on `subscription.authenticated`, so the
        webhook alone cannot say which payment to refund. Verified against a real
        production event: `payload.payment.entity` is absent, and the subscription
        entity carries no `payment_id` either.

        It is on the subscription's invoice, which is what this reads. The mandate
        verification is the first invoice raised against the subscription, so the
        oldest paid one with a payment is the right answer; taking the newest would
        eventually refund a real monthly charge instead.
        """
        payload = self._call(self._client.invoice.all, {"subscription_id": subscription_id})
        items = payload.get("items") or []
        paid = [
            item for item in items if item.get("payment_id") and str(item.get("status")) == "paid"
        ]
        if not paid:
            return None
        paid.sort(key=lambda item: item.get("created_at") or 0)
        return str(paid[0]["payment_id"])

    @retry(**_RETRY)
    def cancel_subscription(
        self, subscription_id: str, *, cancel_at_cycle_end: bool = True
    ) -> dict[str, Any]:
        """Stop recurring billing at the provider.

        Defaults to cycle end: the merchant has paid for the current period and keeps
        it. Cancelling immediately forfeits time they already bought, so that is only
        ever done when the caller asks for it explicitly.
        """
        return self._call(
            self._client.subscription.cancel,
            subscription_id,
            {"cancel_at_cycle_end": 1 if cancel_at_cycle_end else 0},
        )


class RazorpayOAuthClient:
    """Payment-links client for a Technology Partner OAuth access token.

    Razorpay's SDK authenticates with key/secret basic auth and cannot represent a
    partner bearer token. This small client intentionally exposes the same methods
    provisioning needs while keeping OAuth credentials out of logs and responses.
    """

    base_url = "https://api.razorpay.com/v1"

    def __init__(self, access_token: str) -> None:
        if not access_token:
            raise RazorpayPermanentError("Razorpay OAuth access token is missing")
        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        )
        self._timeout = settings.razorpay_timeout_seconds

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        try:
            response = self._session.request(
                method, f"{self.base_url}{path}", timeout=self._timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise RazorpayTransientError(str(exc)) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise RazorpayTransientError(response.text[:500])
        if response.status_code >= 400:
            raise RazorpayPermanentError(response.text[:500])
        try:
            return response.json()
        except ValueError as exc:
            raise RazorpayPermanentError("Razorpay returned an invalid JSON response") from exc

    def create_payment_link(self, **kwargs) -> PaymentLinkResult:
        payload = {
            "amount": kwargs["amount_paise"],
            "currency": "INR",
            "accept_partial": kwargs.get("accept_partial", True),
            "reference_id": kwargs["reference_id"],
            "description": kwargs["description"][:255],
            "customer": {
                "name": kwargs["customer_name"],
                "email": kwargs["customer_email"],
            },
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "notes": kwargs.get("notes") or {},
        }
        if kwargs.get("customer_phone"):
            payload["customer"]["contact"] = kwargs["customer_phone"]
        if kwargs.get("expire_by") is not None:
            payload["expire_by"] = int(kwargs["expire_by"].timestamp())
        return PaymentLinkResult.from_payload(self._request("POST", "/payment_links", json=payload))

    def fetch_payment_link(self, link_id: str) -> PaymentLinkResult:
        return PaymentLinkResult.from_payload(self._request("GET", f"/payment_links/{link_id}"))

    def find_by_reference_id(self, reference_id: str) -> PaymentLinkResult | None:
        payload = self._request("GET", "/payment_links", params={"reference_id": reference_id})
        items = payload.get("payment_links") or []
        return PaymentLinkResult.from_payload(items[0]) if items else None

    def cancel_payment_link(self, link_id: str) -> PaymentLinkResult:
        return PaymentLinkResult.from_payload(
            self._request("POST", f"/payment_links/{link_id}/cancel")
        )


def get_razorpay_client(
    *, key_id: str | None = None, key_secret: str | None = None
) -> RazorpayClient:
    """Build a client for the platform account or a merchant's BYO credentials.

    The platform account here is the DEMO one. Subscription billing has its own
    credentials — see `get_billing_client`.
    """
    return RazorpayClient(key_id=key_id, key_secret=key_secret)


def get_billing_client() -> RazorpayClient:
    """The account Vasooli's own subscription fees are charged on.

    Separate from the platform/demo client on purpose. The demo transacts on the
    platform key and the dashboard states "Test mode" beside it; charging real
    subscription fees through that same key would make that statement false and put
    the guided demo on live rails.
    """
    return RazorpayClient(
        key_id=settings.effective_billing_key_id,
        key_secret=settings.effective_billing_key_secret,
    )
