"""Promise-to-pay tracking. Doc §3 Stage 4, §8.

The promise loop is one of the two things that differentiate this project, so the
resume semantics matter: a broken promise resumes escalation at the tone level it was
paused at, never reset to polite. `tier_at_pause` is what makes that possible.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Index, text
from sqlmodel import Field, SQLModel

from app.core.constants import MAX_AUTOMATED_REMINDERS, PromiseStatus
from app.models.base import datetime as _dt  # noqa: F401  (re-exported for typing)
from app.models.base import (
    enum_column,
    fk_column,
    money_column,
    pk_column,
    timestamp_column,
)


class Promise(SQLModel, table=True):
    __tablename__ = "promises"
    __table_args__ = (
        # Partial unique index: at most one ACTIVE promise per invoice, while any
        # number of resolved ones accumulate as history. A plain UNIQUE(invoice_id)
        # would forbid the second promise a repeat offender makes, which is exactly
        # the behaviour the broken-promise metric exists to measure.
        Index(
            "uq_promises_one_active_per_invoice",
            "invoice_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        CheckConstraint(
            f"tier_at_pause >= 0 AND tier_at_pause <= {MAX_AUTOMATED_REMINDERS}",
            name="ck_promises_tier_range",
        ),
        CheckConstraint(
            "promised_amount_paise IS NULL OR promised_amount_paise > 0",
            name="ck_promises_amount_positive",
        ),
        CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 1",
            name="ck_promises_confidence_range",
        ),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    invoice_id: uuid.UUID = Field(sa_column=fk_column("invoices.id"))

    promised_date: date
    #: Null when the customer committed to a date but not an amount ("I'll sort this
    #: out Friday"). Treated as a promise of the full outstanding balance.
    promised_amount_paise: int | None = Field(default=None, sa_column=money_column(nullable=True))

    source_message_excerpt: str
    extraction_confidence: float

    status: PromiseStatus = Field(
        default=PromiseStatus.ACTIVE,
        sa_column=enum_column(default=PromiseStatus.ACTIVE.value, index=True),
    )

    #: The tier escalation had reached when this promise paused it. On a broken
    #: promise, escalation resumes HERE rather than at tier 1 (Doc §3 Stage 4).
    tier_at_pause: int = 0

    resolved_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))

    @property
    def is_active(self) -> bool:
        return self.status == PromiseStatus.ACTIVE
