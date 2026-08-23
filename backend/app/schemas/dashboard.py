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
    #: Latest customer reply, so "previous communication" is visible at a glance.
    reply_count: int
    last_reply_at: datetime | None
    last_reply_excerpt: str | None
    reminders: list[ReminderView]
    promises: list[PromiseView]
    timeline: list[TimelineEntry]


def money(paise: int) -> str:
    return format_inr(paise)
