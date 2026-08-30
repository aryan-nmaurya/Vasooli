"""Dashboard DTOs. Doc §7."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.core.money import format_inr


class QueueRow(BaseModel):
    """One line of the recovery queue."""

    id: uuid.UUID
    invoice_number: str
    customer_name: str
    amount_display: str
    outstanding_paise: int
    days_overdue: int
    status: str
    tier_label: str
    reason_category: str | None
    payment_url: str | None
    next_action: str
    #: Flags the row in the queue as paused for a dispute, without a second request.
    dispute_open: bool = False
    #: When the money actually arrived. Null for anything not yet recovered.
    recovered_at: datetime | None = None
    #: One plain sentence answering "why is Vasooli doing this?"
    why: str
    why_next: str
    why_state: str


class TimelineEntry(BaseModel):
    """One event on an invoice's history. Doc §7 — provisioned to reconciled."""

    at: datetime
    actor: str
    action: str
    #: Which layer did this, rendered as a badge. Makes the architecture visible
    #: without a slide: AI-drafted vs policy vs Razorpay vs deterministic template.
    provenance: Literal["ai", "policy", "razorpay", "system", "human"]
    summary: str
    detail: dict[str, Any]


#: How one conversation entry is classified. The merchant is being shown a
#: conversation, so the distinction that matters is who spoke and in what capacity —
#: not which database table the row came from.
ConversationKind = Literal[
    "customer_message",
    "system_message",
    "ai_analysis",
    "policy_decision",
    "human_action",
    "payment_event",
]


class ConversationEntry(BaseModel):
    """One turn in the conversation about an invoice.

    Derived entirely from the append-only audit log, not stored separately. A second
    persisted copy of the conversation would be a second thing to keep in step with
    the first, and the audit log already records every event this view needs.
    """

    at: datetime
    kind: ConversationKind
    #: Short label — "Customer", "Tier 2 reminder", "AI analysis", "Policy".
    speaker: str
    #: One line describing what happened.
    headline: str
    #: The actual words, where there were any: the customer's message, the reminder
    #: body, the AI's summary. Null for events that are not utterances.
    body: str | None = None
    #: Extra lines a merchant may want but should not have to read — extracted facts,
    #: confidence, the model that answered.
    meta: dict[str, Any] = {}


class DisputeView(BaseModel):
    """A dispute case as the dashboard shows it."""

    id: uuid.UUID
    status: str
    is_open: bool
    reason: str
    summary: str
    facts: list[str]
    confidence: float
    confidence_display: str
    #: The customer's own words, unedited.
    source_excerpt: str
    detected_by: str
    ai_degraded: bool
    opened_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_note: str | None
    recovery_resumed_at: datetime | None
    #: What the merchant should do next, in one line.
    next_action: str
    #: True when verified payment arrived while this case was open.
    payment_received_while_open: bool


class ReminderView(BaseModel):
    tier: int
    tone: str
    subject: str
    body: str
    generated_by: str
    llm_degraded: bool
    sent_at: datetime | None
    #: The full ✓/✗ check list that approved this send, rendered as text. Doc §5.
    policy_rendered: str | None


class PromiseView(BaseModel):
    id: uuid.UUID
    invoice_number: str
    customer_name: str
    promised_date: str
    amount_display: str
    status: str
    confidence: float
    tier_at_pause: int
    excerpt: str


class InvoiceDetail(BaseModel):
    id: uuid.UUID
    invoice_number: str
    customer_name: str
    customer_email: str
    amount_display: str
    paid_display: str
    outstanding_display: str
    #: The balance split by source. Never collapsed into one number on this screen:
    #: an operator deciding whether to chase has to see which part Razorpay verified
    #: and which part a colleague typed in.
    link_paid_display: str = "₹0"
    external_paid_display: str = "₹0"
    #: Payments recorded by hand, reversed entries included. A balance that once said
    #: "paid" and now says "owed" is exactly the history a customer will ask about.
    external_payments: list[dict] = []
    status: str
    days_overdue: int
    due_at: datetime
    reason_category: str | None
    reason_explanation: str | None
    reason_confidence: float | None
    reason_llm_disagreed: bool
    reminders_sent: int
    current_tier: int
    escalated_to_human_at: datetime | None
    escalation_reason: str | None
    recovered_at: datetime | None
    payment_url: str | None
    payment_link_status: str | None
    why: str
    why_next: str
    why_state: str
    #: True only where the simulated-reply demo control is deliberately enabled.
    #: The dashboard hides the control otherwise rather than rendering a button that
    #: can only return 403.
    simulated_replies_enabled: bool = False
    #: Latest customer reply, so "previous communication" is visible at a glance.
    reply_count: int
    last_reply_at: datetime | None
    last_reply_excerpt: str | None
    #: The open dispute case, if recovery is paused for one. This is what the
    #: "why is recovery paused?" banner is built from.
    dispute: DisputeView | None
    #: Resolved cases, oldest first. History, not the current state.
    dispute_history: list[DisputeView]
    reminders: list[ReminderView]
    promises: list[PromiseView]
    timeline: list[TimelineEntry]
    #: The same events as `timeline`, reshaped into a conversation: who said what,
    #: in order, with the words included.
    conversation: list[ConversationEntry]


def money(paise: int) -> str:
    return format_inr(paise)
