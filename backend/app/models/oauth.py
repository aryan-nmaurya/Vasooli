"""Short-lived, single-use OAuth authorization state."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column, String, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import fk_column, jsonb_column, pk_column, timestamp_column


class OAuthState(SQLModel, table=True):
    __tablename__ = "oauth_states"
    __table_args__ = (UniqueConstraint("provider", "state_hash", name="uq_oauth_state_hash"),)

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    user_id: uuid.UUID = Field(sa_column=fk_column("users.id"))
    provider: str = Field(sa_column=Column(String(40), nullable=False, index=True))
    state_hash: str = Field(sa_column=Column(String(64), nullable=False, index=True))
    redirect_uri: str = Field(sa_column=Column(String(1000), nullable=False))
    state_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", jsonb_column(default=dict).type, nullable=False),
    )
    expires_at: datetime = Field(sa_column=timestamp_column(index=True))
    used_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
