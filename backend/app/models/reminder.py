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

    #: Set only when a provider accepted the message. This — not the row's existence —
    #: is what makes a tier "sent". A row with sent_at NULL is an attempt that failed
    #: and is still owed to the customer.
    sent_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    send_error: str | None = None

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
        return self.sent_at is not None

    @property
    def needs_retry(self) -> bool:
        """A recorded attempt that never reached the customer."""
        return (
            self.sent_at is None
            and self.delivery_state in {"pending", "processing", "failed"}
            and self.attempt_count < MAX_DELIVERY_ATTEMPTS
        )
