"""Invoice recipients, and the payment history that drives diagnosis. Doc §8.

The history fields here are the ONLY inputs the reason classifier is allowed to use
(Doc §3 Stage 2). Keeping them on the customer rather than recomputing per run means
the eval harness and the live system see identical signals.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel

from app.models.base import (
    fk_column,
    money_column,
    pk_column,
    timestamp_column,
    updated_at_column,
)


class Customer(SQLModel, table=True):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint("total_invoices >= 0", name="ck_customers_total_non_negative"),
        CheckConstraint(
            "invoices_paid_late >= 0 AND invoices_paid_late <= total_invoices",
            name="ck_customers_late_within_total",
        ),
        CheckConstraint(
            "invoices_defaulted >= 0 AND invoices_defaulted <= total_invoices",
            name="ck_customers_defaulted_within_total",
        ),
        CheckConstraint("broken_promises >= 0", name="ck_customers_broken_non_negative"),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))

    name: str
    email: str = Field(index=True)
    # Razorpay's Customers API expects a contact number; a customer without one cannot
    # be created there, and Razorpay rejects a Payment Link customer without one.
    phone: str | None = None
    razorpay_customer_id: str | None = Field(default=None, index=True)

    # --- Historical signals. Doc §3 Stage 2. ---
    total_invoices: int = 0
    invoices_paid_late: int = 0
    #: Invoices never paid at all. Distinguishes cash-constrained (always eventually
    #: pays) from unresponsive — the single signal separating those two categories.
    invoices_defaulted: int = 0
    broken_promises: int = 0
    avg_invoice_paise: int = Field(default=0, sa_column=money_column(default=0))

    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    updated_at: datetime = Field(sa_column=updated_at_column())

    @property
    def on_time_rate(self) -> float:
        """Share of invoices paid on time, derived rather than stored.

        Storing this alongside `invoices_paid_late` invites the two to contradict each
        other — a row claiming 100% on-time with 4 late payments would quietly corrupt
        every diagnosis for that customer. Deriving it makes that state unreachable.

        A customer with no history reads as fully reliable, which routes a first-time
        overdue invoice to `oversight`. That matches the spec's definition.
        """
        if self.total_invoices <= 0:
            return 1.0
        return (self.total_invoices - self.invoices_paid_late) / self.total_invoices

    @property
    def has_payment_history(self) -> bool:
        return self.total_invoices > 0

    @property
    def always_eventually_pays(self) -> bool:
        """True when the customer has been late but has never actually defaulted."""
        return self.invoices_paid_late > 0 and self.invoices_defaulted == 0
