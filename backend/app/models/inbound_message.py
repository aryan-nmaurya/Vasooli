"""Verified inbound customer email retained as conversation evidence."""

import uuid
from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel

from app.models.base import fk_column, jsonb_column, pk_column, timestamp_column


class InboundMessage(SQLModel, table=True):
    __tablename__ = "inbound_messages"

    id: uuid.UUID = Field(sa_column=pk_column())
    invoice_id: uuid.UUID = Field(sa_column=fk_column("invoices.id"))
    provider_event_id: str = Field(index=True, unique=True)
    message_id: str = Field(index=True, unique=True)
    sender: str
    recipient: str
    subject: str = ""
    body_text: str
    in_reply_to: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict, sa_column=jsonb_column(default=dict))
    signature_verified: bool = False
    received_at: datetime = Field(sa_column=timestamp_column(default_now=True, index=True))
    processed_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    processing_error: str | None = None
