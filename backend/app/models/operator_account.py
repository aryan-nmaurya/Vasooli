"""Named dashboard operators with independently revocable credentials."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Column, String
from sqlmodel import Field, SQLModel

from app.models.base import bool_column, pk_column, timestamp_column


class OperatorAccount(SQLModel, table=True):
    __tablename__ = "operator_accounts"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'operator', 'auditor')",
            name="ck_operator_accounts_role",
        ),
        CheckConstraint(
            "failed_login_attempts >= 0",
            name="ck_operator_accounts_failed_login_attempts",
        ),
        CheckConstraint(
            "session_version >= 1",
            name="ck_operator_accounts_session_version",
        ),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    username: str = Field(sa_column=Column(String(64), nullable=False, unique=True, index=True))
    display_name: str = Field(sa_column=Column(String(120), nullable=False))
    password_hash: str = Field(sa_column=Column(String(512), nullable=False))
    role: str = Field(default="operator", sa_column=Column(String(20), nullable=False))
    is_active: bool = Field(default=True, sa_column=bool_column(default=True))
    # Incrementing this revokes every session previously issued to the account.
    session_version: int = 1
    failed_login_attempts: int = 0
    locked_until: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    last_login_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    updated_at: datetime = Field(sa_column=timestamp_column(default_now=True))
