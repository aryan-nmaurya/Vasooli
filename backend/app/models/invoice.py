"""Invoices — the centre of the recovery loop. Doc §8."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel

from app.core.clock import days_overdue as _days_overdue
from app.core.constants import (
    MAX_AUTOMATED_REMINDERS,
    InvoiceStatus,
    ReasonCategory,
)
from app.core.money import format_inr
from app.models.base import (
    bool_column,
    enum_column,
    fk_column,
    money_column,
    pk_column,
    timestamp_column,
    updated_at_column,
)


class Invoice(SQLModel, table=True):
    __tablename__ = "invoices"
    __table_args__ = (
        # The reminder cap is enforced at the database, not only in app.policy. A cap
        # that lives solely in Python survives exactly until someone writes a second
        # code path — and "never more than 3 automated contacts" is a compliance
        # promise (Doc §3), not a preference.
        #
        # The literal below is rendered from MAX_AUTOMATED_REMINDERS at import time.
        # The generated migration bakes in today's value, so changing the constant
        # needs a new migration, not just an edit.
        CheckConstraint(
            f"reminders_sent >= 0 AND reminders_sent <= {MAX_AUTOMATED_REMINDERS}",
            name="ck_invoices_reminder_cap",
        ),
        CheckConstraint(
            f"current_tier >= 0 AND current_tier <= {MAX_AUTOMATED_REMINDERS}",
            name="ck_invoices_tier_range",
        ),
        CheckConstraint("amount_paise > 0", name="ck_invoices_amount_positive"),
        # Reconciliation only ever adds. A negative balance means a bug upstream.
        CheckConstraint("amount_paid_paise >= 0", name="ck_invoices_paid_non_negative"),
        CheckConstraint("due_at >= issued_at", name="ck_invoices_due_after_issue"),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    customer_id: uuid.UUID = Field(sa_column=fk_column("customers.id"))

    invoice_number: str = Field(index=True, unique=True)  # "INV-2291"
    amount_paise: int = Field(sa_column=money_column())

    #: Total settled, from every source. Derived — always the sum of the two columns
    #: below — and kept on the row because the policy engine and every dashboard query
    #: read it, and neither should have to add up a ledger to find out whether an
    #: invoice is paid.
    amount_paid_paise: int = Field(default=0, sa_column=money_column(default=0))

    #: The running total Razorpay reports for this invoice's payment link.
    #:
    #: Its own column, and this is load-bearing. Reconciliation applies the provider's
    #: figure with `max()`, which is what makes a duplicate or out-of-order webhook
    #: harmless. If operator-entered payments shared that column, a ₹50,000 bank
    #: transfer recorded by hand would make every subsequent link payment look like a
    #: stale, smaller total — `max()` would discard it, and real money would silently
    #: vanish from the balance. Separating the sources keeps `max()` correct for the
    #: one source it describes.
    link_paid_paise: int = Field(default=0, sa_column=money_column(default=0))

    #: The sum of every non-reversed ExternalPayment: bank transfers, UPI outside the
    #: link, cheques, cash, agreed adjustments. Additive, because each entry is a
    #: distinct transaction rather than a restatement of one running total.
    external_paid_paise: int = Field(default=0, sa_column=money_column(default=0))

    currency: str = "INR"

    issued_at: datetime = Field(sa_column=timestamp_column())
    due_at: datetime = Field(sa_column=timestamp_column(index=True))
    terms_days: int = 30

    status: InvoiceStatus = Field(
        default=InvoiceStatus.PENDING,
        sa_column=enum_column(default=InvoiceStatus.PENDING.value, index=True),
    )

    # --- Diagnosis (Phase 6 writes these; Doc §3 Stage 2) ---
    reason_category: ReasonCategory | None = Field(
        default=None, sa_column=enum_column(nullable=True)
    )
    reason_explanation: str | None = None
    reason_confidence: float | None = None
    reason_diagnosed_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    #: Set when the LLM's category differed from the rule-based result. The rule wins;
    #: the disagreement is kept because it is a reported eval metric (Phase 11).
    reason_llm_disagreed: bool = Field(default=False, sa_column=bool_column())

    # --- Cadence counters, denormalized for the policy engine ---
    # app.policy is a pure function of its arguments, so it cannot run a COUNT(*).
    # These are maintained by app.services whenever a reminder is sent.
    reminders_sent: int = 0
    current_tier: int = 0
    last_reminder_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))

    # --- Customer replies (Doc §3 Stage 2/4) ---------------------------------
    #: Whether the customer has ever answered, and when.
    #:
    #: Persisted on the invoice rather than derived from the audit log, because
    #: diagnosis needs it on every cycle. "Unresponsive" is DEFINED as no reply after
    #: the Tier 2 reminder, so a customer who wrote "I cannot pay until Friday" and is
    #: then classified unresponsive is not a cosmetic error: it changes the tone of the
    #: next message and whether the invoice is handed to a human.
    reply_count: int = 0
    last_reply_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    last_reply_excerpt: str | None = None

    escalated_to_human_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    escalation_reason: str | None = None

    #: A dispute recorded before Vasooli ever contacted the customer. One of the two
    #: routes into `dispute_likely`; the other is a complaint detected in a reply.
    has_prior_dispute_note: bool = Field(default=False, sa_column=bool_column())

    recovered_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))

    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    updated_at: datetime = Field(sa_column=updated_at_column())

    # ------------------------------------------------------------------
    # Derived values. Read-only — no DB access, safe to call from app.policy.
    # ------------------------------------------------------------------

    @property
    def days_overdue(self) -> int:
        """Whole days past due on the IST calendar. Never negative."""
        return _days_overdue(self.due_at)

    @property
    def outstanding_paise(self) -> int:
        return max(0, self.amount_paise - self.amount_paid_paise)

    @property
    def is_fully_paid(self) -> bool:
        """Deliberately `>=`, not `==`.

        An overpayment settles the invoice. Treating it as unpaid would leave the
        invoice in the chase queue after the customer has already sent money — the
        single worst false positive this system can produce.
        """
        return self.amount_paid_paise >= self.amount_paise

    @property
    def amount_display(self) -> str:
        return format_inr(self.amount_paise)

    @property
    def outstanding_display(self) -> str:
        return format_inr(self.outstanding_paise)

    @property
    def has_replied(self) -> bool:
        """Has this customer ever answered a reminder?"""
        return self.reply_count > 0

    @property
    def link_should_be_closed(self) -> bool:
        """No further money should be accepted against this invoice.

        Full payment and a write-off are different decisions with the same consequence
        for the payment route. A settled invoice must not take a second payment; an
        invoice the merchant has given up on must not quietly collect money into a
        balance nobody is reconciling any more. Closure eligibility keys on this rather
        than on `is_fully_paid` alone, which previously left every written-off
        invoice's Razorpay link live indefinitely.
        """
        return self.is_fully_paid or self.status == InvoiceStatus.WRITTEN_OFF

    @property
    def is_in_automation(self) -> bool:
        """False once the invoice is resolved or has been handed to a human."""
        return self.status not in {
            InvoiceStatus.RECOVERED,
            InvoiceStatus.WRITTEN_OFF,
            InvoiceStatus.HUMAN_REVIEW,
        }
