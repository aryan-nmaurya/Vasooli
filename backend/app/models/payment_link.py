"""Razorpay Payment Link, one per invoice. Doc §4.

Originally this was a Smart Collect virtual account — a dedicated bank account per
invoice. Razorpay confirmed Smart Collect is unavailable for this merchant's business
type, so collection runs on Payment Links instead.

What that changes: the customer opens a hosted payment page rather than transferring to
a bank account number. What it does not change: the link is still a real Razorpay
object, payment still arrives as a real signed webhook, and matching is still
deterministic — `notes.invoice_id` and `reference_id` both come back untouched, giving
two independent ways to identify the invoice without guessing.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel

from app.models.base import (
    bool_column,
    fk_column,
    jsonb_column,
    money_column,
    pk_column,
    timestamp_column,
    updated_at_column,
)


class PaymentLinkStatus:
    """Razorpay's own payment-link statuses, mirrored rather than reinvented.

    Keeping their vocabulary means a value in our database can be compared directly
    against the dashboard during a demo, with no translation table in between.
    """

    CREATED = "created"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    #: Statuses where no further money can arrive.
    TERMINAL = frozenset({PAID, CANCELLED, EXPIRED})


#: Stop retrying closure after this many attempts and surface it to an operator.
MAX_CLOSURE_ATTEMPTS = 5


class PaymentLink(SQLModel, table=True):
    __tablename__ = "payment_links"
    __table_args__ = (
        CheckConstraint("amount_expected_paise > 0", name="ck_payment_links_expected_positive"),
        CheckConstraint("amount_paid_paise >= 0", name="ck_payment_links_paid_non_negative"),
        CheckConstraint(
            "amount_refunded_paise >= 0", name="ck_payment_links_refunded_non_negative"
        ),
    )

    id: uuid.UUID = Field(sa_column=pk_column())

    # UNIQUE: provisioning is retried on failure and re-run across batches. Without
    # this, a retry creates a second payment link for the same invoice and the
    # customer could be sent two different places to pay the same bill.
    invoice_id: uuid.UUID = Field(sa_column=fk_column("invoices.id", unique=True))

    razorpay_payment_link_id: str = Field(index=True, unique=True)  # "plink_XXXXXXXX"
    #: Our own identifier, echoed back by Razorpay. The second deterministic match path
    #: if `notes` is ever absent from a webhook payload.
    reference_id: str = Field(index=True, unique=True)

    #: The URL that goes into the reminder email. Stored rather than refetched so
    #: drafting never depends on a live Razorpay call.
    short_url: str

    status: str = PaymentLinkStatus.CREATED
    amount_expected_paise: int = Field(sa_column=money_column())
    amount_paid_paise: int = Field(default=0, sa_column=money_column(default=0))
    amount_refunded_paise: int = Field(default=0, sa_column=money_column(default=0))
    #: Partial payment is allowed deliberately — a customer paying half is a customer
    #: paying, and refusing it would push them back to "I'll sort it out later".
    accept_partial: bool = Field(default=True, sa_column=bool_column(default=True))

    #: Full creation response, kept verbatim for the audit trail.
    raw_response: dict[str, Any] = Field(default_factory=dict, sa_column=jsonb_column(default=dict))

    provisioned_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    cancelled_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))

    # --- Closure after recovery ----------------------------------------------
    #: Razorpay is called AFTER the payment is committed, never inside the
    #: reconciliation transaction: an external call held open across a database
    #: transaction turns a slow API into a lock held on the invoice row.
    #:
    #: So closure can fail while the payment is already safely recorded. These fields
    #: make that failure a retryable operational task rather than a silent
    #: inconsistency where a recovered invoice still has a live payment link.
    closure_attempts: int = 0
    closure_error: str | None = None
    next_closure_retry_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    updated_at: datetime = Field(sa_column=updated_at_column())

    @property
    def is_open(self) -> bool:
        """True while this link can still receive money."""
        return self.status not in PaymentLinkStatus.TERMINAL

    @property
    def needs_closure(self) -> bool:
        """Closure has not been confirmed, and attempts remain.

        Keyed on `cancelled_at` — set only when closure actually completed — rather
        than on `status`, which reconciliation sets from the webhook payload and which
        would therefore mark the link done before Razorpay was ever told.
        """
        return self.cancelled_at is None and self.closure_attempts < MAX_CLOSURE_ATTEMPTS

    @property
    def outstanding_paise(self) -> int:
        return max(0, self.amount_expected_paise - self.amount_paid_paise)
