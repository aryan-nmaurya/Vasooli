"""Versioned merchant recovery controls and outbound safety state."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Column, String, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import fk_column, jsonb_column, pk_column, timestamp_column, updated_at_column


class ReminderPolicyVersion(SQLModel, table=True):
    __tablename__ = "reminder_policy_versions"
    __table_args__ = (
        UniqueConstraint("merchant_id", "version", name="uq_policy_merchant_version"),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    version: int = Field(default=1)
    tier_offsets: list[int] = Field(
        default_factory=lambda: [3, 10, 21], sa_column=jsonb_column(default=lambda: [3, 10, 21])
    )
    cooldown_days: int = 7
    max_attempts: int = 3
    timezone: str = Field(default="Asia/Kolkata", sa_column=Column(String(80), nullable=False))
    channel: str = Field(default="email", sa_column=Column(String(30), nullable=False))
    sending_window: dict[str, Any] = Field(
        default_factory=dict, sa_column=jsonb_column(default=dict)
    )
    pause_conditions: dict[str, Any] = Field(
        default_factory=dict, sa_column=jsonb_column(default=dict)
    )
    is_active: bool = True
    created_by_user_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("users.id", nullable=True)
    )
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))


class SuppressionEntry(SQLModel, table=True):
    __tablename__ = "suppression_entries"

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    customer_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("customers.id", nullable=True)
    )
    email: str | None = Field(
        default=None, sa_column=Column(String(320), nullable=True, index=True)
    )
    reason: str = Field(sa_column=Column(String(40), nullable=False))
    source: str = Field(default="merchant", sa_column=Column(String(40), nullable=False))
    active: bool = True
    expires_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))


class SendingDomain(SQLModel, table=True):
    __tablename__ = "sending_domains"
    __table_args__ = (UniqueConstraint("merchant_id", "domain", name="uq_sending_domain_merchant"),)

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    domain: str = Field(sa_column=Column(String(255), nullable=False))
    status: str = Field(default="pending", sa_column=Column(String(30), nullable=False, index=True))
    verification_token: str = Field(sa_column=Column(String(160), nullable=False))
    provider: str = Field(default="resend", sa_column=Column(String(30), nullable=False))
    provider_domain_id: str | None = Field(
        default=None, sa_column=Column(String(160), nullable=True, unique=True)
    )
    local_part: str = Field(default="accounts", sa_column=Column(String(64), nullable=False))
    dns_records: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=jsonb_column(default=list)
    )
    verified_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    updated_at: datetime = Field(sa_column=updated_at_column())


class MerchantUsageBucket(SQLModel, table=True):
    __tablename__ = "merchant_usage_buckets"
    __table_args__ = (UniqueConstraint("merchant_id", "bucket_date", name="uq_usage_merchant_day"),)

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    bucket_date: date
    sent_count: int = 0
    failed_count: int = 0
    quota: int = 0
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    updated_at: datetime = Field(sa_column=updated_at_column())
