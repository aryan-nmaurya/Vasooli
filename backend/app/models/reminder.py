"""Escalation log — one row per reminder attempt. Doc §8."""

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

    sent_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    send_error: str | None = None

    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))

    @property
    def was_sent(self) -> bool:
        return self.sent_at is not None
