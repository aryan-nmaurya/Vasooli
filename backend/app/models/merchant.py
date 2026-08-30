"""The owning business. Doc §8."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Column, String
from sqlmodel import Field, SQLModel

from app.models.base import (
    bool_column,
    jsonb_column,
    pk_column,
    timestamp_column,
    updated_at_column,
)


class MerchantMode:
    DEMO = "demo"
    LIVE = "live"


class MerchantStatus:
    ONBOARDING = "onboarding"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class Merchant(SQLModel, table=True):
    __tablename__ = "merchants"
    __table_args__ = (
        CheckConstraint("mode IN ('demo', 'live')", name="ck_merchants_mode"),
        CheckConstraint(
            "(mode = 'demo' AND is_demo) OR (mode = 'live' AND NOT is_demo)",
            name="ck_merchants_mode_flag",
        ),
        CheckConstraint(
            "status IN ('onboarding', 'active', 'suspended', 'closed')",
            name="ck_merchants_status",
        ),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    name: str
    # Where customer replies are routed and what appears in the reminder signature.
    contact_email: str
    reply_to_email: str | None = None

    legal_name: str | None = Field(default=None, sa_column=Column(String(240), nullable=True))
    country: str = Field(default="IN", sa_column=Column(String(2), nullable=False))
    timezone: str = Field(default="Asia/Kolkata", sa_column=Column(String(80), nullable=False))
    mode: str = Field(default=MerchantMode.DEMO, sa_column=Column(String(12), nullable=False))
    status: str = Field(default=MerchantStatus.ACTIVE, sa_column=Column(String(20), nullable=False))
    is_demo: bool = Field(default=True, sa_column=bool_column(default=True))
    onboarding_state: dict[str, Any] = Field(
        default_factory=dict, sa_column=jsonb_column(default=dict)
    )
    terms_accepted_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    privacy_accepted_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))

    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    updated_at: datetime = Field(sa_column=updated_at_column())
