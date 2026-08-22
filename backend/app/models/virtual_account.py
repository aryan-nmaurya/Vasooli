"""Razorpay Smart Collect virtual account, one per invoice. Doc §4, §8."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel

from app.models.base import (
    fk_column,
    jsonb_column,
    money_column,
    pk_column,
    timestamp_column,
    updated_at_column,
)


class VirtualAccountStatus:
    ACTIVE = "active"
    CLOSED = "closed"
    PAID = "paid"


class VirtualAccount(SQLModel, table=True):
    __tablename__ = "virtual_accounts"
    __table_args__ = (
        CheckConstraint("amount_expected_paise > 0", name="ck_va_expected_positive"),
        CheckConstraint("amount_paid_paise >= 0", name="ck_va_paid_non_negative"),
    )

    id: uuid.UUID = Field(sa_column=pk_column())

    # UNIQUE: provisioning is retried on failure and re-run across batches. Without
    # this, a retry creates a second virtual account and payments split across two
    # bank references that reconcile against the same invoice.
    invoice_id: uuid.UUID = Field(sa_column=fk_column("invoices.id", unique=True))

    razorpay_va_id: str = Field(index=True, unique=True)  # "va_XXXXXXXX"
    razorpay_customer_id: str

    status: str = VirtualAccountStatus.ACTIVE
    amount_expected_paise: int = Field(sa_column=money_column())
    amount_paid_paise: int = Field(default=0, sa_column=money_column(default=0))

    # The payable details that go into the reminder email. Stored rather than fetched
    # so drafting never depends on a live Razorpay call, and so the audit trail shows
    # exactly what the customer was told to pay into.
    bank_account_name: str | None = None
    bank_account_number: str | None = None
    bank_ifsc: str | None = None

    #: Full creation response, kept verbatim for the audit trail.
    raw_response: dict[str, Any] = Field(default_factory=dict, sa_column=jsonb_column(default=dict))

    provisioned_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    closed_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    updated_at: datetime = Field(sa_column=updated_at_column())

    @property
    def payable_reference(self) -> str:
        """What a customer would type into their bank's transfer form."""
        if not self.bank_account_number or not self.bank_ifsc:
            return ""
        return f"{self.bank_account_number} / {self.bank_ifsc}"
