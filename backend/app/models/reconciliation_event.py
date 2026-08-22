"""Raw webhook deliveries and their reconciliation outcome. Doc §6, §8.

Razorpay delivers at-least-once, so the same `virtual_account.credited` event can
arrive several times. Deduplication happens on the unique index below rather than in
application memory: an in-process set forgets on restart and is not shared across
workers, and either failure double-counts recovered revenue.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel

from app.models.base import (
    bool_column,
    fk_column,
    jsonb_column,
    money_column,
    pk_column,
    timestamp_column,
)


class ReconciliationEvent(SQLModel, table=True):
    __tablename__ = "reconciliation_events"

    id: uuid.UUID = Field(sa_column=pk_column())

    #: The idempotency key. The webhook handler inserts this row BEFORE processing;
    #: a duplicate delivery raises IntegrityError and is acknowledged without effect.
    provider_event_id: str = Field(index=True, unique=True)

    event_type: str = Field(index=True)
    raw_payload: dict[str, Any] = Field(default_factory=dict, sa_column=jsonb_column(default=dict))

    #: Recorded rather than assumed. A row can only exist if the HMAC verified, but
    #: storing the flag keeps the audit trail explicit about it.
    signature_verified: bool = Field(default=False, sa_column=bool_column())

    matched_invoice_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("invoices.id", nullable=True)
    )
    #: How the invoice was found: va_id | notes | customer_id. Kept because an event
    #: that matched only by the fallback path is worth noticing.
    match_strategy: str | None = None

    amount_paise: int | None = Field(default=None, sa_column=money_column(nullable=True))

    processed_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    #: Set when reconciliation could not complete — an unmatched payment, most often.
    #: Surfaces in the dashboard as "needs manual matching" instead of being lost.
    processing_error: str | None = None

    received_at: datetime = Field(sa_column=timestamp_column(default_now=True, index=True))

    @property
    def is_processed(self) -> bool:
        return self.processed_at is not None
