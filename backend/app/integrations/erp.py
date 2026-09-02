"""Provider-neutral ERP adapter contract used by sync workers and APIs."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

import httpx

from app.integrations.outbound_url import assert_safe_outbound_url

#: Zoho Books is the only supported ERP. Everything that validates a provider
#: reads this rather than repeating a literal.
SUPPORTED_PROVIDERS = frozenset({"zoho"})


@dataclass(frozen=True)
class CanonicalInvoice:
    source_system: str
    source_tenant: str
    source_id: str
    source_version: str | None
    updated_at: datetime | None
    invoice_number: str
    customer_name: str
    customer_email: str
    amount_paise: int
    issued_at: datetime
    due_at: datetime
    currency: str = "INR"
    tombstoned: bool = False
    paid_paise: int = 0
    credited_paise: int = 0
    raw_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class SyncPage:
    records: list[CanonicalInvoice]
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class CanonicalCustomer:
    source_system: str
    source_tenant: str
    source_id: str
    name: str
    email: str | None
    phone: str | None = None
    consent: str | None = None


@dataclass(frozen=True)
class CanonicalPayment:
    source_system: str
    source_tenant: str
    source_id: str
    invoice_source_id: str
    amount_paise: int
    paid_at: datetime
    currency: str = "INR"


@dataclass(frozen=True)
class CanonicalCreditNote:
    source_system: str
    source_tenant: str
    source_id: str
    invoice_source_id: str
    amount_paise: int
    issued_at: datetime
    currency: str = "INR"


class ErpAdapter(Protocol):
    provider: str

    def fetch_invoices(self, *, cursor: str | None, limit: int = 100) -> SyncPage: ...


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _invoice_from_mapping(provider: str, row: dict[str, Any], *, tenant: str) -> CanonicalInvoice:
    issued = _parse_datetime(row.get("issued_at") or row.get("date") or row.get("invoice_date"))
    due = _parse_datetime(row.get("due_at") or row.get("due_date"))
    if issued is None or due is None:
        raise ValueError("ERP invoice is missing a valid issued and due date")
    amount = row.get("amount_paise")
    if amount is None:
        amount = int(
            (Decimal(str(row.get("total", row.get("total_amount", 0)))) * 100).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    total_paise = int(amount)
    payments = row.get("payments") or []
    credits = row.get("credit_notes") or row.get("creditnotes") or []

    def _sum_rows(values: Any) -> int:
        if not isinstance(values, list):
            return 0
        result = 0
        for value in values:
            if not isinstance(value, dict):
                continue
            raw = value.get("amount_paise")
            if raw is None:
                raw = Decimal(str(value.get("amount") or 0)) * 100
            result += int(Decimal(str(raw)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return result

    credited_paise = int(
        row.get("credited_paise")
        or row.get("credits_applied_paise")
        or (Decimal(str(row.get("credits_applied") or 0)) * 100)
    ) or _sum_rows(credits)
    paid_raw = row.get("paid_paise") or row.get("amount_paid_paise")
    if paid_raw is None and row.get("payment_made") is not None:
        paid_raw = Decimal(str(row.get("payment_made") or 0)) * 100
    paid_paise = int(paid_raw or 0) or _sum_rows(payments)
    if not paid_paise and row.get("balance") is not None:
        balance_paise = int(
            (Decimal(str(row.get("balance") or 0)) * 100).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        paid_paise = max(0, total_paise - balance_paise - credited_paise)
    if row.get("status") in {"paid", "closed"} and not paid_paise:
        paid_paise = max(0, total_paise - credited_paise)

    return CanonicalInvoice(
        source_system=provider,
        source_tenant=tenant,
        source_id=str(row.get("source_id") or row.get("invoice_id") or row.get("id")),
        source_version=str(row.get("source_version") or row.get("version") or "") or None,
        updated_at=_parse_datetime(row.get("updated_at") or row.get("last_modified_time")),
        invoice_number=str(
            row.get("invoice_number") or row.get("invoice_number_text") or row["id"]
        ),
        customer_name=str(row.get("customer_name") or row.get("customer_name_text") or "Unknown"),
        customer_email=str(row.get("customer_email") or row.get("email") or ""),
        amount_paise=total_paise,
        issued_at=issued,
        due_at=due,
        currency=str(row.get("currency") or "INR"),
        tombstoned=bool(row.get("tombstoned", False) or row.get("status") in {"void", "cancelled"}),
        paid_paise=paid_paise,
        credited_paise=credited_paise,
        raw_payload=row,
    )


class ErpAuthExpiredError(RuntimeError):
    """The stored OAuth access token is no longer accepted.

    Separate from a transient provider error because the remedy is different: this
    one is fixed by refreshing the token and retrying, and retrying without
    refreshing would fail identically forever.
    """


class ZohoBooksAdapter:
    """Read-only, cursor-based Zoho Books invoice adapter.

    Credentials are supplied by the encrypted connection record and never exposed
    through the API. The adapter uses ``last_modified_time`` as its incremental cursor
    and honors the provider API domain returned during OAuth.
    """

    provider = "zoho"

    def __init__(self, credentials: dict[str, Any], *, source_tenant: str | None = None) -> None:
        self.access_token = str(credentials.get("access_token") or "")
        self.organization_id = str(credentials.get("organization_id") or "")
        self.api_domain = str(credentials.get("api_domain") or "https://www.zohoapis.com")
        self.source_tenant = source_tenant or self.organization_id or "default"
        self.timeout = credentials.get("timeout_seconds") or 20
        if not self.access_token or not self.organization_id:
            raise ValueError("Zoho requires access_token and organization_id")
        assert_safe_outbound_url(self.api_domain, what="Zoho API domain")

    def fetch_invoices(self, *, cursor: str | None, limit: int = 100) -> SyncPage:
        page = int(cursor or "1")
        params: dict[str, Any] = {
            "organization_id": self.organization_id,
            "page": page,
            "per_page": min(max(limit, 1), 200),
            "sort_column": "last_modified_time",
            "sort_order": "A",
        }
        if cursor and not cursor.isdigit():
            params["last_modified_time"] = cursor
            params["page"] = 1
        response = httpx.get(
            f"{self.api_domain.rstrip('/')}/books/v3/invoices",
            headers={"Authorization": f"Zoho-oauthtoken {self.access_token}"},
            params=params,
            timeout=self.timeout,
            follow_redirects=False,
        )
        if response.status_code == 401:
            # Zoho access tokens last about an hour, so this is the ordinary state of
            # a scheduled sync rather than an exceptional one.
            raise ErpAuthExpiredError("Zoho access token expired")
        if response.status_code == 429 or response.status_code >= 500:
            raise RuntimeError(f"Zoho transient error ({response.status_code})")
        if response.is_error:
            raise RuntimeError(f"Zoho rejected invoice sync ({response.status_code})")
        payload = response.json()
        rows = payload.get("invoices") or []
        records = [_invoice_from_mapping("zoho", row, tenant=self.source_tenant) for row in rows]
        has_more = bool((payload.get("page_context") or {}).get("has_more_page"))
        next_cursor = str(page + 1) if has_more else None
        return SyncPage(records=records, next_cursor=next_cursor, has_more=has_more)


def json_safe(payload: dict[str, Any]) -> dict[str, Any]:
    """A copy of `payload` that Postgres JSON and `json.dumps` will both accept.

    An adapter builds `CanonicalInvoice` from the same dict it stores as
    `raw_payload`, and the canonical fields are typed `datetime` — so a source row
    carrying real dates is both required by the contract and unserializable by it.
    Every invoice has dates, so this was not an edge case: it raised
    "Object of type datetime is not JSON serializable" inside `_upsert_record`,
    after `ingest_batch` had already written the invoices. The sync recorded itself
    as failed, wrote a retry that would fail identically, and left the ledger rows
    behind with no `erp_records` entry to dedupe against on the next run.

    Dates become ISO-8601 strings; anything else JSON cannot hold falls back to its
    string form rather than aborting the run.
    """
    return json.loads(json.dumps(payload, sort_keys=True, default=str))


def payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(canonical).hexdigest()


def adapter_for(provider: str, payload: list[dict[str, Any]] | None = None) -> ErpAdapter:
    """A deterministic in-memory adapter over supplied rows.

    A test and fixture seam only: it never reaches a provider, which is what makes
    the sync pipeline testable without a live Zoho tenant. Reaching a real provider
    goes through `adapter_for_credentials`.
    """
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported ERP provider: {provider}")
    return FixtureAdapter(payload or [], provider=provider)


def adapter_for_credentials(
    provider: str,
    credentials: dict[str, Any],
    *,
    source_tenant: str | None = None,
) -> ErpAdapter:
    if provider == "zoho":
        return ZohoBooksAdapter(credentials, source_tenant=source_tenant)
    raise ValueError(f"Unsupported ERP provider: {provider}")


class FixtureAdapter:
    """Replays supplied rows through the same canonical pipeline as a real provider."""

    def __init__(self, rows: list[dict[str, Any]], *, provider: str = "zoho") -> None:
        self._rows = rows
        self.provider = provider

    def fetch_invoices(self, *, cursor: str | None, limit: int = 100) -> SyncPage:
        start = int(cursor or 0)
        selected = self._rows[start : start + limit]
        records: list[CanonicalInvoice] = []
        for row in selected:
            records.append(
                CanonicalInvoice(
                    source_system=self.provider,
                    source_tenant=str(row.get("source_tenant") or "default"),
                    source_id=str(row["source_id"]),
                    source_version=str(row.get("source_version"))
                    if row.get("source_version")
                    else None,
                    updated_at=row.get("updated_at"),
                    invoice_number=str(row["invoice_number"]),
                    customer_name=str(row["customer_name"]),
                    customer_email=str(row["customer_email"]),
                    amount_paise=int(row["amount_paise"]),
                    issued_at=row["issued_at"],
                    due_at=row["due_at"],
                    currency=str(row.get("currency") or "INR"),
                    tombstoned=bool(row.get("tombstoned", False)),
                    paid_paise=int(row.get("paid_paise") or row.get("amount_paid_paise") or 0),
                    credited_paise=int(row.get("credited_paise") or 0),
                    raw_payload=row,
                )
            )
        next_index = start + len(selected)
        return SyncPage(
            records,
            str(next_index) if next_index < len(self._rows) else None,
            next_index < len(self._rows),
        )
