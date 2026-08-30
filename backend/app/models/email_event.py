"""What the mail provider says happened to a message after it accepted it.

The audit's finding was blunt and correct: `sent_at` was stamped the moment Resend's
API returned 2xx, and the product then described that as delivery. An accepted API call
means the provider took custody of the message. It does not mean a mail server accepted
it, that it reached an inbox, or that the address exists — a hard bounce arrives
seconds to minutes later, asynchronously, on a webhook nobody was listening to.

The practical consequence is worse than a wording problem. An invoice whose reminders
all bounce shows three reminders sent, advances through the tiers on schedule, and is
eventually escalated as an unresponsive customer who in fact never received a word.

These rows are the provider's own account of what happened, stored verbatim and
deduplicated on the provider's event id.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel

from app.models.base import fk_column, jsonb_column, pk_column, timestamp_column


class DeliveryState:
    """Where a message got to. Ordered by how far it travelled.

    `SENT` means only that the provider accepted it — deliberately named for what it
    is, so no part of the codebase can read it as delivery.
    """

    SENT = "sent"
    DELIVERED = "delivered"
    #: A mail server refused it permanently: the address does not exist, or the domain
    #: rejects us. Retrying is pointless and repeated attempts damage sender reputation.
    BOUNCED = "bounced"
    #: A soft failure — mailbox full, greylisted, temporary DNS. The provider may
    #: retry; we do not treat it as final.
    DEFERRED = "deferred"
    #: Marked as spam by the recipient. Never mail this address again without asking.
    COMPLAINED = "complained"

    #: States where the customer certainly did not get the message.
    FAILED = frozenset({BOUNCED})
    #: States where nothing further should be sent to this address automatically.
    SUPPRESSING = frozenset({BOUNCED, COMPLAINED})


#: Resend's event names, mapped to our vocabulary. Unlisted events are stored for the
#: trail and change no state — an `email.opened` is not evidence about delivery, and
#: acting on it would be tracking rather than operations.
PROVIDER_EVENT_STATES: dict[str, str] = {
    "email.sent": DeliveryState.SENT,
    "email.delivered": DeliveryState.DELIVERED,
    "email.bounced": DeliveryState.BOUNCED,
    "email.delivery_delayed": DeliveryState.DEFERRED,
    "email.complained": DeliveryState.COMPLAINED,
}


class EmailEvent(SQLModel, table=True):
    __tablename__ = "email_events"

    id: uuid.UUID = Field(sa_column=pk_column())
    #: Nullable: a provider can report on a message we no longer have a reminder for,
    #: and dropping the event would lose a bounce.
    reminder_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("reminders.id", nullable=True)
    )
    invoice_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("invoices.id", nullable=True)
    )

    #: The idempotency key. Providers deliver at-least-once, exactly like Razorpay.
    provider_event_id: str = Field(index=True, unique=True)
    provider_message_id: str | None = Field(default=None, index=True)
    event_type: str = Field(index=True)
    state: str | None = Field(default=None, index=True)

    recipient: str = ""
    detail: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict, sa_column=jsonb_column(default=dict))

    occurred_at: datetime = Field(sa_column=timestamp_column(default_now=True, index=True))
    received_at: datetime = Field(sa_column=timestamp_column(default_now=True))
