"""Append-only record of every decision and action. Doc §3 Stage 6, §8.

Append-only is enforced by a database trigger, not by convention — see the Phase 1
migration. UPDATE and DELETE on this table raise, including for the owner, so the
guarantee holds against a stray ORM call or a hand-typed psql statement.

The demo shows this table; a log that could have been edited proves nothing.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel

from app.models.base import fk_column, jsonb_column, pk_column, timestamp_column


class AuditActor:
    """Who took the action. Rendered as provenance badges on the invoice timeline."""

    SYSTEM = "system"
    AI = "ai"
    POLICY = "policy"
    RAZORPAY = "razorpay"
    SCHEDULER = "scheduler"

    @staticmethod
    def human(email: str) -> str:
        return f"human:{email}"


class AuditAction:
    """Canonical action names. Strings, not an enum, because this list grows every
    phase and the dashboard filters on it — a new value should never need a migration.
    """

    INVOICE_INGESTED = "invoice_ingested"
    VA_PROVISIONED = "va_provisioned"
    VA_PROVISION_FAILED = "va_provision_failed"
    VA_CLOSED = "va_closed"
    DIAGNOSED = "diagnosed"
    LLM_FAILOVER = "llm_failover"
    LLM_FALLBACK_TEMPLATE = "llm_fallback_template"
    POLICY_EVALUATED = "policy_evaluated"
    POLICY_REJECTED = "policy_rejected"
    REMINDER_SENT = "reminder_sent"
    REMINDER_FAILED = "reminder_failed"
    REPLY_RECEIVED = "reply_received"
    PROMISE_LOGGED = "promise_logged"
    PROMISE_KEPT = "promise_kept"
    PROMISE_BROKEN = "promise_broken"
    ESCALATED_TO_HUMAN = "escalated_to_human"
    PAYMENT_RECONCILED = "payment_reconciled"
    RECONCILIATION_UNMATCHED = "reconciliation_unmatched"
    WEBHOOK_DUPLICATE_IGNORED = "webhook_duplicate_ignored"
    WEBHOOK_SIGNATURE_INVALID = "webhook_signature_invalid"


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: uuid.UUID = Field(sa_column=pk_column())
    #: Nullable: some events (an unmatched payment, an invalid signature) belong to no
    #: invoice, and those are precisely the ones worth keeping.
    invoice_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("invoices.id", nullable=True)
    )

    actor: str = Field(index=True)
    action: str = Field(index=True)
    #: Structured context — the policy check list, the webhook payload, the failover
    #: reason. JSONB so the dashboard can filter on it without a schema change.
    detail: dict[str, Any] = Field(default_factory=dict, sa_column=jsonb_column(default=dict))

    created_at: datetime = Field(sa_column=timestamp_column(default_now=True, index=True))
