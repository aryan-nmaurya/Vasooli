"""Append-only record of every decision and action. Doc §3 Stage 6, §8.

Append-only is enforced by a database trigger, not by convention — see the Phase 1
migration. UPDATE and DELETE on this table raise for every role, so the guarantee
holds against a stray ORM call or a hand-typed psql statement.

What it does not stop: TRUNCATE does not fire a row-level trigger, and a table owner
can drop the trigger. This is tamper-evidence against the application and ordinary
DML — not against someone with owner rights on the database.

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
    PAYMENT_LINK_CREATED = "payment_link_created"
    PAYMENT_LINK_FAILED = "payment_link_failed"
    VA_CLOSED = "va_closed"
    DIAGNOSED = "diagnosed"
    LLM_FAILOVER = "llm_failover"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_OUTPUT_REJECTED = "llm_output_rejected"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"
    LLM_FALLBACK_TEMPLATE = "llm_fallback_template"
    POLICY_EVALUATED = "policy_evaluated"
    POLICY_REJECTED = "policy_rejected"
    REMINDER_SENT = "reminder_sent"
    REMINDER_FAILED = "reminder_failed"
    #: What the mail provider reported AFTER accepting the message: delivered,
    #: bounced, deferred, or marked as spam. Separate from REMINDER_SENT, which only
    #: ever meant the provider took custody of it.
    REMINDER_DELIVERY_UPDATED = "reminder_delivery_updated"
    #: Automated email stopped for an invoice because the address permanently refused
    #: it or the recipient marked it as spam.
    CONTACT_SUPPRESSED = "contact_suppressed"
    REPLY_RECEIVED = "reply_received"
    #: A stored inbound message that could not be interpreted. Retryable, and visible
    #: in the exceptions queue rather than only in a column nobody queries.
    INBOUND_PROCESSING_FAILED = "inbound_processing_failed"
    INBOUND_REPROCESSED = "inbound_reprocessed"
    PROMISE_LOGGED = "promise_logged"
    PROMISE_KEPT = "promise_kept"
    PROMISE_BROKEN = "promise_broken"
    ESCALATED_TO_HUMAN = "escalated_to_human"
    #: An operator gave up on an invoice. Kept as a constant rather than a literal at
    #: the call site so the dashboard filter and the summary map cannot drift from it.
    INVOICE_WRITTEN_OFF = "written_off"
    # --- Customer conversation safety ---------------------------------------
    #: The AI read a dispute in a customer's reply. An observation, not a decision.
    DISPUTE_DETECTED = "dispute_detected"
    #: The policy engine acted on that observation. This is the decision.
    RECOVERY_PAUSED = "recovery_paused"
    DISPUTE_CASE_OPENED = "dispute_case_opened"
    #: A repeat of a message that already opened the case — recorded so the trail
    #: shows the replay was seen and deliberately did nothing.
    DISPUTE_ALREADY_OPEN = "dispute_already_open"
    DISPUTE_RESOLVED = "dispute_resolved"
    RECOVERY_RESUMED = "recovery_resumed"
    #: Money arrived while a dispute was open. Razorpay is still the truth.
    PAYMENT_DURING_DISPUTE = "payment_during_dispute"
    # --- Demo controls -------------------------------------------------------
    #: The simulated clock was moved. Recorded because the clock changes what the
    #: system believes "now" is, and every decision below it inherits that.
    DEMO_CLOCK_ADVANCED = "demo_clock_advanced"
    DEMO_CLOCK_RESET = "demo_clock_reset"
    #: Reminder mail was pointed at a different inbox. Outbound mail is the
    #: system's one side effect on the outside world; where it went is recorded.
    DEMO_EMAIL_REDIRECTED = "demo_email_redirected"
    PAYMENT_RECONCILED = "payment_reconciled"
    # --- Money recorded by a person, not by a provider -----------------------
    #: A bank transfer, UPI, cheque, or agreed adjustment entered by an operator. The
    #: detail carries "verification": "operator_asserted" so the trail never lets this
    #: be mistaken for a signed Razorpay event.
    EXTERNAL_PAYMENT_RECORDED = "external_payment_recorded"
    #: A recorded payment retracted. The row survives; the balance is recomputed.
    EXTERNAL_PAYMENT_REVERSED = "external_payment_reversed"
    #: An unmatched Razorpay settlement an operator tied to an invoice by hand.
    RECONCILIATION_MANUALLY_MATCHED = "reconciliation_manually_matched"
    PAYMENT_LINK_CLOSED = "payment_link_closed"
    PAYMENT_LINK_CLOSE_FAILED = "payment_link_close_failed"
    PAYMENT_LINK_CLOSE_RETRIED = "payment_link_close_retried"
    RECONCILIATION_UNMATCHED = "reconciliation_unmatched"
    RECONCILIATION_FAILED = "reconciliation_failed"
    RECONCILIATION_RETRIED = "reconciliation_retried"
    RECONCILIATION_SYNCED = "reconciliation_synced"
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
