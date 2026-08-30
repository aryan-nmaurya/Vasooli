"""Verified inbound customer email retained as conversation evidence."""

import uuid
from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel

from app.models.base import fk_column, jsonb_column, pk_column, timestamp_column

#: Give up automatic reprocessing after this many attempts and leave it for a person.
#: A message the extractor cannot handle will not start working on the sixth try; what
#: it needs is somebody to read it.
MAX_INBOUND_ATTEMPTS = 5


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

    # --- Reprocessing --------------------------------------------------------
    #: The webhook is answered 200 as soon as the message is stored — that is what
    #: stops the provider redelivering. Before these fields existed, a message whose
    #: processing raised was therefore a dead end: the provider considered it
    #: delivered, nothing retried it, and no operator action could reprocess it. A
    #: customer's "we already paid this on the 14th" could vanish into a column nobody
    #: queries while the reminders kept going out.
    processing_attempts: int = 0
    last_attempt_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    #: Bounded exponential backoff, swept by the same job that retries deliveries,
    #: closures, and webhook events.
    next_retry_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))

    @property
    def is_processed(self) -> bool:
        return self.processed_at is not None

    @property
    def needs_retry(self) -> bool:
        """Failed, and automatic attempts remain."""
        return (
            self.processed_at is None
            and self.processing_error is not None
            and self.processing_attempts < MAX_INBOUND_ATTEMPTS
        )

    @property
    def is_exhausted(self) -> bool:
        """Out of automatic attempts. A person has to read this one."""
        return (
            self.processed_at is None
            and self.processing_error is not None
            and self.processing_attempts >= MAX_INBOUND_ATTEMPTS
        )
