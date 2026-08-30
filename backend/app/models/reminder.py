"""Escalation log — one row per reminder tier, carrying its delivery attempts. Doc §8.

A row is created when a send is ATTEMPTED, and `sent_at` is stamped only when a
provider accepts it. The distinction matters: before it existed, a failed send still
occupied the tier slot, the recovery cycle treated that tier as done, and the invoice
was stranded — a customer who never received a reminder was never chased again.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.core.constants import MAX_AUTOMATED_REMINDERS, Tone
from app.models.base import (
    bool_column,
    enum_column,
    fk_column,
    jsonb_column,
    pk_column,
    timestamp_column,
)

#: Give up after this many failed deliveries and leave it for a human. An address that
#: has bounced five times is not going to start working.
MAX_DELIVERY_ATTEMPTS = 5


class Reminder(SQLModel, table=True):
    __tablename__ = "reminders"
    __table_args__ = (
        # One reminder per tier per invoice. This is the structural guarantee that a
        # scheduler restart mid-cycle, or two overlapping cycles, cannot send Tier 2
        # twice — the insert fails rather than the customer being contacted again.
        UniqueConstraint("invoice_id", "tier", name="uq_reminders_invoice_tier"),
        CheckConstraint(
            f"tier >= 1 AND tier <= {MAX_AUTOMATED_REMINDERS}", name="ck_reminders_tier_range"
        ),
        CheckConstraint(
            "delivery_state IN ('pending', 'processing', 'sent', 'failed', 'dead')",
            name="ck_reminders_delivery_state",
        ),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    invoice_id: uuid.UUID = Field(sa_column=fk_column("invoices.id"))

    tier: int
    tone: Tone = Field(sa_column=enum_column())

    subject: str
    body: str
    channel: str = "email"

    provider: str | None = None  # resend | sendgrid | dry_run
    provider_message_id: str | None = None

    #: The complete policy check list that approved this send, rendered on the invoice
    #: timeline and in the audit log (Doc §5). Stored on the reminder so the decision
    #: and the message it approved can never drift apart.
    policy_decision: dict[str, Any] = Field(
        default_factory=dict, sa_column=jsonb_column(default=dict)
    )

    #: Model id that drafted this, or "template_fallback" when both models were down.
    generated_by: str = "template_fallback"
    llm_degraded: bool = Field(default=False, sa_column=bool_column())

    #: Set only when a provider ACCEPTED the message — which is custody, not delivery.
    #: This is what makes a tier "sent". A row with sent_at NULL is an attempt that
    #: failed and is still owed to the customer.
    sent_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    send_error: str | None = None

    # --- What the provider said happened afterwards --------------------------
    #: Delivery is asynchronous and the provider reports it on a webhook, seconds to
    #: minutes after the API call returned. Without these, "sent" was the last thing
    #: this system ever knew about a message, and an invoice whose every reminder hard
    #: bounced still advanced through the tiers and was escalated as an unresponsive
    #: customer who had in fact never received a word.
    delivery_status: str | None = Field(default=None, index=True)
    delivered_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    bounced_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    #: The provider's own reason string, kept verbatim. "Mailbox does not exist" and
    #: "message refused as spam" need different responses from a person.
    delivery_detail: str | None = None
    last_delivery_event_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))

    # --- Delivery attempts ---------------------------------------------------
    #: Every attempt, successful or not. A row can be retried in place, so this is the
    #: only record of how hard we tried.
    attempt_count: int = 0
    last_attempt_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    #: When the next retry becomes eligible. Bounded exponential backoff, so a provider
    #: outage produces a handful of spaced attempts rather than a retry storm that
    #: looks like an attack on our own mail provider.
    next_retry_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    #: Transactional-outbox state. A worker commits `processing` and a lease before
    #: touching the provider; an expired lease is reclaimable after a process crash.
    delivery_state: str = "pending"
    lease_token: str | None = None
    lease_expires_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))

    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))

    @property
    def was_sent(self) -> bool:
        """The provider accepted it. Deliberately NOT named `was_delivered`."""
        return self.sent_at is not None

    @property
    def reached_the_customer(self) -> bool:
        """The provider confirmed delivery, and nothing later took it back.

        The strongest statement this system can honestly make about a reminder. Still
        not "the customer read it" — nothing here tracks opens, and an open pixel would
        not be evidence anyway.

        The bounce condition is not redundant. A message can be accepted by a receiving
        mail server and bounce minutes later, which is an ordinary asynchronous bounce
        and leaves both timestamps set. Reading only `delivered_at` would then report
        that a reminder reached a customer who never saw it — the precise false
        reassurance this whole delivery-tracking change exists to remove.
        """
        return self.delivered_at is not None and self.bounced_at is None

    @property
    def hard_failed(self) -> bool:
        """Accepted by the provider, then permanently refused by the recipient."""
        return self.bounced_at is not None

    @property
    def needs_retry(self) -> bool:
        """A recorded attempt that never reached the customer."""
        return (
            self.sent_at is None
            and self.delivery_state in {"pending", "processing", "failed"}
            and self.attempt_count < MAX_DELIVERY_ATTEMPTS
        )
