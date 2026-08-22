"""Shared column helpers for SQLModel entities.

Three conventions are applied here rather than repeated on every field:

* **Timestamps are TIMESTAMPTZ.** SQLModel maps a bare `datetime` to a naive
  `DateTime`, which silently drops the offset. Overdue-day math depends on knowing
  what instant a timestamp refers to, so every one of them carries its timezone.
* **Enums are stored as VARCHAR**, not native Postgres enums. Adding a value to a PG
  enum requires a migration and cannot be done inside a transaction on older versions;
  these values will change during the build, and a VARCHAR column absorbs that freely.
* **Money columns are BIGINT paise.** A 32-bit INTEGER caps at about ₹2.1 crore, which
  a B2B receivables ledger can plausibly exceed.

Each helper returns a NEW Column instance on every call — SQLAlchemy Column objects
bind to exactly one table, so a shared module-level instance would silently attach to
whichever model imported it first.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

from app.core.clock import utcnow


def pk_column() -> Column:
    """UUID primary key.

    UUIDs over autoincrement so ids can be generated before insert — provisioning
    passes an invoice id to Razorpay in `notes` inside the same transaction that
    creates the row.
    """
    return Column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def fk_column(
    target: str, *, nullable: bool = False, index: bool = True, unique: bool = False
) -> Column:
    """Foreign key to `<table>.<column>`, e.g. "invoices.id".

    `unique` belongs here rather than on Field(): SQLModel refuses to combine
    `Field(unique=True)` with an explicit `sa_column`.
    """
    return Column(
        PgUUID(as_uuid=True),
        ForeignKey(target),
        nullable=nullable,
        index=index,
        unique=unique,
    )


def money_column(*, default: int | None = None, nullable: bool = False) -> Column:
    """Integer paise. Never NUMERIC, never float — see app/core/money.py."""
    return Column(BigInteger, nullable=nullable, default=default)


def enum_column(*, default: Any = None, nullable: bool = False, index: bool = False) -> Column:
    """StrEnum stored as VARCHAR."""
    return Column(String, nullable=nullable, default=default, index=index)


def timestamp_column(
    *, nullable: bool = False, default_now: bool = False, index: bool = False
) -> Column:
    """Timezone-aware timestamp.

    `default_now` reads through app.core.clock, so rows created during a demo
    fast-forward carry the shifted time and the audit trail stays internally coherent.
    """
    return Column(
        DateTime(timezone=True),
        nullable=nullable,
        default=utcnow if default_now else None,
        index=index,
    )


def updated_at_column() -> Column:
    return Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


def jsonb_column(*, nullable: bool = False, default: Any = None) -> Column:
    """JSONB, not JSON — indexable and stored parsed.

    Used for raw provider payloads and audit detail, where the shape is the
    provider's to define and ours to keep verbatim.
    """
    return Column(JSONB, nullable=nullable, default=default)


def bool_column(*, default: bool = False) -> Column:
    return Column(Boolean, nullable=False, default=default)


__all__ = [
    "bool_column",
    "datetime",
    "enum_column",
    "fk_column",
    "jsonb_column",
    "money_column",
    "pk_column",
    "timestamp_column",
    "updated_at_column",
]
