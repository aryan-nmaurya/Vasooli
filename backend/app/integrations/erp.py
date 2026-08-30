"""Provider-neutral ERP adapter contract used by sync workers and APIs."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


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
    raw_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class SyncPage:
    records: list[CanonicalInvoice]
    next_cursor: str | None
    has_more: bool


class ErpAdapter(Protocol):
    provider: str

    def fetch_invoices(self, *, cursor: str | None, limit: int = 100) -> SyncPage: ...


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
    """Return a deterministic fixture adapter until provider credentials are configured."""
    if provider == "custom":
        return CustomFixtureAdapter(payload or [])
    if provider in {"zoho", "tally"}:
        raise ValueError(f"{provider} adapter requires an active connector worker")
    raise ValueError(f"Unsupported ERP provider: {provider}")


class CustomFixtureAdapter:
    provider = "custom"

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetch_invoices(self, *, cursor: str | None, limit: int = 100) -> SyncPage:
        start = int(cursor or 0)
        selected = self._rows[start : start + limit]
        records: list[CanonicalInvoice] = []
        for row in selected:
            records.append(
                CanonicalInvoice(
                    source_system="custom",
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
                    raw_payload=row,
                )
            )
        next_index = start + len(selected)
        return SyncPage(
            records,
            str(next_index) if next_index < len(self._rows) else None,
            next_index < len(self._rows),
        )
