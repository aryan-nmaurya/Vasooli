"""Recovery metrics. Doc §7, §9.

Defined once and imported by both the dashboard API and the Phase 11 eval harness.
Two implementations of "recovery rate" will drift, and the dashboard disagreeing with
the evaluation report while a panel is watching is a bad moment.

The important definition is that **recovery rate is measured by value, not by count**.
Recovering forty ₹2,000 invoices and missing one ₹80,000 invoice is a 97% success rate
by count and a 50% failure by money — and money is what the merchant actually cares
about.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, select

from app.core.constants import InvoiceStatus, PromiseStatus
from app.core.money import format_inr
from app.models import Invoice, Promise
from app.services.demo_scope import demo_invoices

#: Invoices no longer being pursued.
CLOSED_STATUSES = (InvoiceStatus.RECOVERED, InvoiceStatus.WRITTEN_OFF)


@dataclass(frozen=True)
class RecoveryMetrics:
    total_overdue_paise: int
    recovered_paise: int
    recovery_rate: float
    avg_days_to_recovery: float | None
    automation_rate: float | None
    invoices_total: int
    invoices_recovered: int
    invoices_in_human_review: int
    active_promises: int
    broken_promises: int
    counts_by_status: dict[str, int]
    counts_by_reason: dict[str, int]

    def as_dict(self) -> dict:
        return {
            "total_overdue_paise": self.total_overdue_paise,
            "total_overdue_display": format_inr(self.total_overdue_paise),
            "recovered_paise": self.recovered_paise,
            "recovered_display": format_inr(self.recovered_paise),
            "recovery_rate": round(self.recovery_rate, 4),
            "recovery_rate_display": f"{self.recovery_rate * 100:.1f}%",
            "avg_days_to_recovery": (
                round(self.avg_days_to_recovery, 1)
                if self.avg_days_to_recovery is not None
                else None
            ),
            "automation_rate": (
                round(self.automation_rate, 4) if self.automation_rate is not None else None
            ),
            "automation_rate_display": (
                f"{self.automation_rate * 100:.0f}%" if self.automation_rate is not None else "—"
            ),
            "invoices_total": self.invoices_total,
            "invoices_recovered": self.invoices_recovered,
            "invoices_in_human_review": self.invoices_in_human_review,
            "active_promises": self.active_promises,
            "broken_promises": self.broken_promises,
            "counts_by_status": self.counts_by_status,
            "counts_by_reason": self.counts_by_reason,
        }


def compute_metrics(
    session: Session,
    *,
    since: datetime | None = None,
    merchant_id=None,
) -> RecoveryMetrics:
    """Everything the overview panel shows, from one pass over the ledger."""
    # The overview is the demo console's headline. Without this filter a live
    # merchant's receivables were counted into the demo's totals the moment anyone
    # registered — see app.services.demo_scope.
    invoice_query = (
        select(Invoice).where(Invoice.merchant_id == merchant_id)
        if merchant_id is not None
        else demo_invoices()
    )
    invoices = list(session.exec(invoice_query).all())
    invoice_ids = [invoice.id for invoice in invoices]

    recovered = [i for i in invoices if i.status == InvoiceStatus.RECOVERED]
    if since is not None:
        recovered = [i for i in recovered if i.recovered_at and i.recovered_at >= since]

    # Outstanding balance on everything still being pursued.
    total_overdue = sum(
        i.amount_paise - i.amount_paid_paise for i in invoices if i.status not in CLOSED_STATUSES
    )
    recovered_paise = sum(i.amount_paid_paise for i in recovered)

    denominator = recovered_paise + total_overdue
    recovery_rate = recovered_paise / denominator if denominator else 0.0

    # Days from due date to money landing — the number a merchant feels.
    spans = [
        (i.recovered_at - i.due_at).total_seconds() / 86400
        for i in recovered
        if i.recovered_at and i.due_at
    ]
    avg_days = sum(spans) / len(spans) if spans else None

    # Resolved without a human ever touching it. Doc §9.
    automation_rate = (
        sum(1 for i in recovered if i.escalated_to_human_at is None) / len(recovered)
        if recovered
        else None
    )

    counts_by_status: dict[str, int] = {}
    for invoice in invoices:
        key = str(invoice.status)
        counts_by_status[key] = counts_by_status.get(key, 0) + 1

    counts_by_reason: dict[str, int] = {}
    for invoice in invoices:
        if invoice.reason_category:
            key = str(invoice.reason_category)
            counts_by_reason[key] = counts_by_reason.get(key, 0) + 1

    promise_scope = Promise.invoice_id.in_(invoice_ids)  # type: ignore[union-attr]
    active_promises = session.exec(
        select(func.count())
        .select_from(Promise)
        .where(promise_scope, Promise.status == PromiseStatus.ACTIVE)
    ).one()
    broken_promises = session.exec(
        select(func.count())
        .select_from(Promise)
        .where(promise_scope, Promise.status == PromiseStatus.BROKEN)
    ).one()

    return RecoveryMetrics(
        total_overdue_paise=total_overdue,
        recovered_paise=recovered_paise,
        recovery_rate=recovery_rate,
        avg_days_to_recovery=avg_days,
        automation_rate=automation_rate,
        invoices_total=len(invoices),
        invoices_recovered=len(recovered),
        invoices_in_human_review=sum(1 for i in invoices if i.status == InvoiceStatus.HUMAN_REVIEW),
        active_promises=_scalar(active_promises),
        broken_promises=_scalar(broken_promises),
        counts_by_status=counts_by_status,
        counts_by_reason=counts_by_reason,
    )


def _scalar(value) -> int:
    """SQLModel's exec() hands back a Row for aggregate queries, not an int."""
    return int(value[0]) if isinstance(value, tuple) else int(value)
