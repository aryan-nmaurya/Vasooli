"""Vasooli subscription billing and entitlement ledger."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Column, String, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import (
    bool_column,
    fk_column,
    jsonb_column,
    money_column,
    pk_column,
    timestamp_column,
)


class BillingPlan(SQLModel, table=True):
    __tablename__ = "billing_plans"
    __table_args__ = (UniqueConstraint("slug", "version", name="uq_billing_plans_slug_version"),)

    id: uuid.UUID = Field(sa_column=pk_column())
    slug: str = Field(sa_column=Column(String(40), nullable=False, index=True))
    version: int = Field(default=1)
    name: str = Field(sa_column=Column(String(80), nullable=False))
    razorpay_plan_id: str | None = Field(default=None, sa_column=Column(String(120), unique=True))
    amount_paise: int = Field(sa_column=money_column())
    included_active_invoices: int
    included_seats: int = 5
    is_active: bool = Field(default=True, sa_column=bool_column(default=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))


class BillingCustomer(SQLModel, table=True):
    __tablename__ = "billing_customers"

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id", unique=True))
    provider_customer_id: str = Field(sa_column=Column(String(120), nullable=False, unique=True))
    billing_email: str = Field(sa_column=Column(String(320), nullable=False))
    legal_name: str | None = Field(default=None, sa_column=Column(String(240), nullable=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))


class BillingSubscription(SQLModel, table=True):
    __tablename__ = "billing_subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'created','authenticated','active','past_due','paused','cancelled','expired'"
            ")",
            name="ck_billing_subscription_status",
        ),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    plan_id: uuid.UUID = Field(sa_column=fk_column("billing_plans.id"))
    razorpay_subscription_id: str | None = Field(
        default=None, sa_column=Column(String(120), unique=True, index=True)
    )
    status: str = Field(default="created", sa_column=Column(String(20), nullable=False, index=True))
    current_period_start: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    current_period_end: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    grace_until: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    cancel_at_period_end: bool = Field(default=False, sa_column=bool_column(default=False))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    updated_at: datetime = Field(sa_column=timestamp_column(default_now=True))


class BillingEvent(SQLModel, table=True):
    __tablename__ = "billing_events"

    id: uuid.UUID = Field(sa_column=pk_column())
    provider_event_id: str = Field(
        sa_column=Column(String(160), nullable=False, unique=True, index=True)
    )
    event_type: str = Field(sa_column=Column(String(100), nullable=False, index=True))
    signature_verified: bool = Field(default=False, sa_column=bool_column(default=False))
    payload_hash: str = Field(sa_column=Column(String(64), nullable=False))
    raw_payload: dict[str, Any] = Field(default_factory=dict, sa_column=jsonb_column(default=dict))
    received_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    processed_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    outcome: str | None = Field(default=None, sa_column=Column(String(80), nullable=True))


class BillingEntitlement(SQLModel, table=True):
    __tablename__ = "billing_entitlements"
    __table_args__ = (
        UniqueConstraint("merchant_id", "feature", name="uq_billing_entitlement_feature"),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    feature: str = Field(sa_column=Column(String(80), nullable=False))
    value: int = Field(default=0)
    source: str = Field(default="plan", sa_column=Column(String(80), nullable=False))
    effective_from: datetime = Field(sa_column=timestamp_column(default_now=True))
    effective_until: datetime | None = Field(sa_column=timestamp_column(nullable=True))


class BillingInvoice(SQLModel, table=True):
    __tablename__ = "billing_invoices"

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    subscription_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("billing_subscriptions.id", nullable=True)
    )
    provider_invoice_id: str | None = Field(
        default=None, sa_column=Column(String(120), unique=True)
    )
    amount_paise: int = Field(sa_column=money_column())
    status: str = Field(default="issued", sa_column=Column(String(30), nullable=False))
    issued_at: datetime = Field(sa_column=timestamp_column(default_now=True))


class BillingPaymentAttempt(SQLModel, table=True):
    __tablename__ = "billing_payment_attempts"

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    subscription_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("billing_subscriptions.id", nullable=True)
    )
    provider_payment_id: str | None = Field(
        default=None, sa_column=Column(String(120), unique=True)
    )
    amount_paise: int = Field(sa_column=money_column())
    status: str = Field(default="created", sa_column=Column(String(30), nullable=False))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))


class BillingRefund(SQLModel, table=True):
    __tablename__ = "billing_refunds"

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    provider_refund_id: str = Field(sa_column=Column(String(120), nullable=False, unique=True))
    amount_paise: int = Field(sa_column=money_column())
    status: str = Field(default="created", sa_column=Column(String(30), nullable=False))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))


class BillingReconciliationRun(SQLModel, table=True):
    """Durable evidence of the daily provider-vs-ledger comparison."""

    __tablename__ = "billing_reconciliation_runs"

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("merchants.id", nullable=True)
    )
    status: str = Field(default="running", sa_column=Column(String(30), nullable=False, index=True))
    checked_count: int = 0
    drift_count: int = 0
    detail: dict[str, Any] = Field(default_factory=dict, sa_column=jsonb_column(default=dict))
    error: str | None = Field(default=None, sa_column=Column(String(1000), nullable=True))
    started_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    finished_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
