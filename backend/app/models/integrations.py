"""Tenant-owned ERP connection, canonical record and sync failure state."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column, String, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import fk_column, jsonb_column, pk_column, timestamp_column, updated_at_column


class ErpConnection(SQLModel, table=True):
    __tablename__ = "erp_connections"
    __table_args__ = (
        UniqueConstraint("merchant_id", "provider", name="uq_erp_connection_provider"),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    provider: str = Field(sa_column=Column(String(40), nullable=False))
    source_tenant: str | None = Field(default=None, sa_column=Column(String(160), nullable=True))
    status: str = Field(default="pending", sa_column=Column(String(30), nullable=False, index=True))
    credentials_encrypted: str | None = Field(
        default=None, sa_column=Column(String(2000), nullable=True)
    )
    cursor: str | None = Field(default=None, sa_column=Column(String(500), nullable=True))
    last_sync_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    last_success_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    freshness_deadline: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    updated_at: datetime = Field(sa_column=updated_at_column())


class ErpSyncRun(SQLModel, table=True):
    __tablename__ = "erp_sync_runs"

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    connection_id: uuid.UUID = Field(sa_column=fk_column("erp_connections.id"))
    status: str = Field(default="running", sa_column=Column(String(30), nullable=False, index=True))
    cursor_before: str | None = Field(default=None, sa_column=Column(String(500), nullable=True))
    cursor_after: str | None = Field(default=None, sa_column=Column(String(500), nullable=True))
    imported_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    error: str | None = Field(default=None, sa_column=Column(String(1000), nullable=True))
    started_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    finished_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))


class ErpRecord(SQLModel, table=True):
    __tablename__ = "erp_records"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id",
            "provider",
            "source_tenant",
            "record_type",
            "source_record_id",
            name="uq_erp_record_identity",
        ),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    connection_id: uuid.UUID = Field(sa_column=fk_column("erp_connections.id"))
    provider: str = Field(sa_column=Column(String(40), nullable=False, index=True))
    source_tenant: str = Field(sa_column=Column(String(160), nullable=False))
    record_type: str = Field(sa_column=Column(String(40), nullable=False))
    source_record_id: str = Field(sa_column=Column(String(180), nullable=False))
    source_version: str | None = Field(default=None, sa_column=Column(String(120), nullable=True))
    source_updated_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    tombstoned: bool = False
    payload_hash: str = Field(sa_column=Column(String(64), nullable=False))
    raw_payload: dict[str, Any] = Field(default_factory=dict, sa_column=jsonb_column(default=dict))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    updated_at: datetime = Field(sa_column=updated_at_column())


class IntegrationFailure(SQLModel, table=True):
    __tablename__ = "integration_failures"

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    connection_id: uuid.UUID = Field(sa_column=fk_column("erp_connections.id"))
    sync_run_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("erp_sync_runs.id", nullable=True)
    )
    category: str = Field(sa_column=Column(String(50), nullable=False))
    source_record_id: str | None = Field(default=None, sa_column=Column(String(180), nullable=True))
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=jsonb_column(default=dict))
    error: str = Field(sa_column=Column(String(1000), nullable=False))
    attempts: int = 0
    next_retry_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    resolved_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
