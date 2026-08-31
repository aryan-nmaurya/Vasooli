"""Append-only ledger for money moving through merchant Razorpay accounts."""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import fk_column, jsonb_column, money_column, pk_column, timestamp_column


class CollectionLedgerEntry(SQLModel, table=True):
    """Immutable evidence of a verified collection event.

    The subscription ledger is deliberately separate. This row records customer
    money in a merchant's own Razorpay account and is keyed by provider event ID so
    replayed webhooks cannot create a second financial entry.
    """

    __tablename__ = "collection_ledger_entries"
    __table_args__ = (
        UniqueConstraint("merchant_id", "provider_event_id", name="uq_collection_ledger_event"),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    invoice_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("invoices.id", nullable=True)
    )
    provider_event_id: str = Field(sa_column=Column(String(180), nullable=False, index=True))
    event_type: str = Field(sa_column=Column(String(80), nullable=False))
    amount_paise: int = Field(default=0, sa_column=money_column(default=0))
    currency: str = Field(default="INR", sa_column=Column(String(3), nullable=False))
    provider_reference: str | None = Field(
        default=None, sa_column=Column(String(180), nullable=True)
    )
    payload: dict = Field(default_factory=dict, sa_column=jsonb_column(default=dict))
    recorded_at: datetime = Field(sa_column=timestamp_column(default_now=True, index=True))
