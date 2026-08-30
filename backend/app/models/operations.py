"""Auditable merchant data requests used by support and launch operations."""

import uuid
from datetime import datetime

from sqlalchemy import Column, String
from sqlmodel import Field, SQLModel

from app.models.base import fk_column, jsonb_column, pk_column, timestamp_column, updated_at_column


class DataRequest(SQLModel, table=True):
    __tablename__ = "data_requests"

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    requested_by_user_id: uuid.UUID = Field(sa_column=fk_column("users.id"))
    request_type: str = Field(sa_column=Column(String(30), nullable=False, index=True))
    status: str = Field(
        default="requested", sa_column=Column(String(30), nullable=False, index=True)
    )
    reason: str | None = Field(default=None, sa_column=Column(String(500), nullable=True))
    artifact_uri: str | None = Field(default=None, sa_column=Column(String(1000), nullable=True))
    detail: dict = Field(default_factory=dict, sa_column=jsonb_column(default=dict))
    completed_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    updated_at: datetime = Field(sa_column=updated_at_column())
