"""Verified email identity for live merchants.

Demo operators deliberately remain in ``operator_accounts``.  Sharing either their
credentials or sessions with live users would turn the reviewer shortcut into a live
authorization path.
"""

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


class UserStatus:
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"
    ALL = frozenset({PENDING, ACTIVE, SUSPENDED, DELETED})


class User(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'active', 'suspended', 'deleted')",
            name="ck_users_status",
        ),
        CheckConstraint("failed_login_attempts >= 0", name="ck_users_failed_login_attempts"),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    email: str = Field(sa_column=Column(String(320), nullable=False, unique=True, index=True))
    display_name: str | None = Field(default=None, sa_column=Column(String(160), nullable=True))
    password_hash: str = Field(sa_column=Column(String(512), nullable=False))
    status: str = Field(default=UserStatus.PENDING, sa_column=Column(String(20), nullable=False))
    is_email_verified: bool = Field(default=False, sa_column=bool_column(default=False))
    email_verified_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    password_changed_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    last_login_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    last_login_ip: str | None = Field(default=None, sa_column=Column(String(64), nullable=True))
    failed_login_attempts: int = 0
    locked_until: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    mfa_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=jsonb_column(default=dict))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    updated_at: datetime = Field(sa_column=updated_at_column())
