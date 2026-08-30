"""Per-merchant Razorpay collection credentials and connection state."""

import uuid
from datetime import datetime

from sqlalchemy import Column, String
from sqlmodel import Field, SQLModel

from app.models.base import (
    fk_column,
    jsonb_column,
    pk_column,
    timestamp_column,
    updated_at_column,
)


class PaymentConnection(SQLModel, table=True):
    __tablename__ = "razorpay_connections"

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id", unique=True))
    provider: str = Field(default="razorpay", sa_column=Column(String(30), nullable=False))
    mode: str = Field(default="oauth", sa_column=Column(String(20), nullable=False))
    provider_account_id: str | None = Field(
        default=None, sa_column=Column(String(120), nullable=True)
    )
    access_token_encrypted: str | None = Field(
        default=None, sa_column=Column(String(1000), nullable=True)
    )
    refresh_token_encrypted: str | None = Field(
        default=None, sa_column=Column(String(1000), nullable=True)
    )
    api_key_id: str | None = Field(default=None, sa_column=Column(String(160), nullable=True))
    api_key_secret_encrypted: str | None = Field(
        default=None, sa_column=Column(String(1000), nullable=True)
    )
    scopes: list[str] = Field(default_factory=list, sa_column=jsonb_column(default=list))
    token_expires_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    status: str = Field(default="pending", sa_column=Column(String(30), nullable=False, index=True))
    last_verified_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    revoked_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    updated_at: datetime = Field(sa_column=updated_at_column())
