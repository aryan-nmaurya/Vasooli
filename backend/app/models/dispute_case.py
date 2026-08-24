"""Human-review case for a disputed invoice. Customer Conversation Safety.

One row per dispute a customer raised in a reply. It exists so that "recovery is
paused" is a fact with a reason, an author and a resolution, rather than an invoice
status that happens to be excluded from a query.

Two things are worth stating about what this table is *not*:

**It is not a case-management platform.** Two states — open, resolved. A dispute is
either being worked or it is finished. The only question the state answers is whether
the automated cadence may run, and that question is binary.

**It is not financial truth.** Nothing here can change what an invoice is worth or
what has been paid against it. The AI's reading of the customer's words is stored as
evidence for a person to act on — `reason`, `summary`, `facts`, `confidence` — and a
person's decision is what closes the case.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, text
from sqlmodel import Field, SQLModel

from app.core.constants import DisputeStatus
from app.models.base import (
    bool_column,
    enum_column,
    fk_column,
    jsonb_column,
    pk_column,
    timestamp_column,
)


class DisputeCase(SQLModel, table=True):
    __tablename__ = "dispute_cases"
    __table_args__ = (
        # At most one OPEN case per invoice, while resolved ones accumulate as
        # history. This index is the idempotency guarantee: replaying the same reply,
        # or a customer repeating their complaint, cannot produce a second open case
        # even if two workers race — the database refuses the insert.
        Index(
            "uq_dispute_cases_one_open_per_invoice",
            "invoice_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_dispute_cases_confidence_range",
        ),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    invoice_id: uuid.UUID = Field(sa_column=fk_column("invoices.id"))

    status: DisputeStatus = Field(
        default=DisputeStatus.OPEN,
        sa_column=enum_column(default=DisputeStatus.OPEN.value, index=True),
    )

    #: A short phrase naming what is being disputed, in the customer's terms —
    #: "quantity short-delivered", "billed for goods returned". Free text on purpose:
    #: a fixed taxonomy would force every real complaint into the nearest wrong box.
    reason: str
    #: One or two sentences a merchant can read instead of the raw message.
    summary: str
    #: Discrete claims the customer made, each one checkable against a delivery note
    #: or a purchase order. Stored as a list of strings, not prose, because the point
    #: is that a person can tick them off one at a time.
    facts: list[Any] = Field(default_factory=list, sa_column=jsonb_column(default=list))
    #: How sure the model was. Shown to the merchant; NOT used to decide the pause.
    #: A low-confidence dispute signal is still a reason to stop chasing, because
    #: chasing someone who is unhappy is the expensive mistake.
    confidence: float = 0.0

    #: The customer's own words, so the merchant judges the message and not only our
    #: reading of it.
    source_excerpt: str
    #: Hash of the reply this case came from. Lets a replay of the identical message
    #: be recognised as a replay rather than a second complaint.
    source_fingerprint: str = Field(index=True)
    #: Which reply number on the invoice raised it.
    source_reply_number: int = 0

    #: Model that produced the analysis, or "rule_based" when none was available.
    detected_by: str = "rule_based"
    #: True when the primary model did not answer — the fallback model or the
    #: deterministic path did.
    ai_degraded: bool = Field(default=False, sa_column=bool_column())

    opened_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    resolved_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    #: The operator who closed it, as "human:<email>" — the same form the audit log
    #: uses for an actor, so provenance reads identically in both places.
    resolved_by: str | None = None
    resolution_note: str | None = None
    #: Set only when the human chose to put the invoice back into the cadence.
    #: A resolved dispute does not resume recovery by itself.
    recovery_resumed_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))

    @property
    def is_open(self) -> bool:
        return self.status == DisputeStatus.OPEN
