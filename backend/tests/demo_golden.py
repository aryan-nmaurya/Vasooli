"""Deterministic capture helpers for the frozen demo regression oracle.

The production implementation plan makes the existing demo a compatibility
contract.  These helpers deliberately live under ``tests``: they are not a second
demo implementation, only a recorder for the behaviour that already exists.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.ai import DiagnosisInputs, DraftInputs, diagnose, draft_reminder
from app.core.clock import now_ist
from app.models import Customer, Invoice
from app.policy import evaluate_reminder, next_tier_for
from app.services.ingestion import ingest_batch
from app.services.provisioning import provision_for_invoice
from scripts.demo_reset import DEMO_SET, to_row

_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_ISO_DATETIME = re.compile(
    r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b"
)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DISPLAY_DATETIME = re.compile(r"\b\d{1,2} [A-Z][a-z]{2} \d{4}, \d{2}:\d{2}\b")


class BaselineRazorpay:
    """Network-free provider seam with stable, realistic link responses."""

    def create_payment_link(self, **kwargs):
        from app.integrations.razorpay_client import PaymentLinkResult

        invoice_number = kwargs["notes"]["invoice_number"]
        suffix = invoice_number.replace("-", "_")
        return PaymentLinkResult.from_payload(
            {
                "id": f"plink_BASELINE_{suffix}",
                "short_url": f"https://rzp.io/rzp/baseline-{invoice_number.lower()}",
                "reference_id": kwargs["reference_id"],
                "status": "created",
                "amount": kwargs["amount_paise"],
                "amount_paid": 0,
                "notes": kwargs["notes"],
            }
        )


def seed_frozen_demo(session: Session) -> list[Invoice]:
    """Seed the eight canonical invoices and their payment links."""

    report = ingest_batch(session, [to_row(item) for item in DEMO_SET], rebase_dates=True)
    assert report.ingested == len(DEMO_SET)
    assert report.failed == 0

    invoices = list(session.exec(select(Invoice).order_by(Invoice.invoice_number)).all())
    provider = BaselineRazorpay()
    for invoice in invoices:
        provision_for_invoice(session, invoice.id, client=provider)
    return invoices


def capture_ledger(session: Session) -> dict[str, Any]:
    """Stable dump of every seeded field that gives the demo its narrative."""

    customers = {row.id: row for row in session.exec(select(Customer)).all()}
    invoices = list(session.exec(select(Invoice).order_by(Invoice.invoice_number)).all())
    narrative = {item.number: item.demonstrates for item in DEMO_SET}

    return {
        "invoice_count": len(invoices),
        "invoices": [
            {
                "invoice_number": invoice.invoice_number,
                "customer": customers[invoice.customer_id].name,
                "customer_email": customers[invoice.customer_id].email,
                "customer_phone": customers[invoice.customer_id].phone,
                "amount_paise": invoice.amount_paise,
                "days_overdue": invoice.days_overdue,
                "terms_days": invoice.terms_days,
                "customer_history": {
                    "total_invoices": customers[invoice.customer_id].total_invoices,
                    "invoices_paid_late": customers[invoice.customer_id].invoices_paid_late,
                    "invoices_defaulted": customers[invoice.customer_id].invoices_defaulted,
                    "broken_promises": customers[invoice.customer_id].broken_promises,
                    "avg_invoice_paise": customers[invoice.customer_id].avg_invoice_paise,
                },
                "has_prior_dispute_note": invoice.has_prior_dispute_note,
                "status": str(invoice.status),
                "reminders_sent": invoice.reminders_sent,
                "current_tier": invoice.current_tier,
                "demonstrates": narrative[invoice.invoice_number],
            }
            for invoice in invoices
        ],
    }


def capture_policy_traces(session: Session) -> dict[str, Any]:
    """Record the complete ten-check decision made for every demo invoice."""

    customers = {row.id: row for row in session.exec(select(Customer)).all()}
    invoices = list(session.exec(select(Invoice).order_by(Invoice.invoice_number)).all())
    captured_at = now_ist()
    traces: list[dict[str, Any]] = []

    for invoice in invoices:
        customer = customers[invoice.customer_id]
        tier = next_tier_for(days_overdue=invoice.days_overdue, sent_tiers=frozenset()) or 1
        diagnosis = diagnose(
            DiagnosisInputs(
                total_invoices=customer.total_invoices,
                invoices_paid_late=customer.invoices_paid_late,
                invoices_defaulted=customer.invoices_defaulted,
                broken_promises=customer.broken_promises,
                avg_invoice_paise=customer.avg_invoice_paise,
                amount_paise=invoice.amount_paise,
                days_overdue=invoice.days_overdue,
                has_prior_dispute_note=invoice.has_prior_dispute_note,
                has_reply=invoice.has_replied,
                reply_has_complaint=False,
                current_tier=invoice.current_tier,
            ),
            invoice_number=invoice.invoice_number,
            use_llm=False,
        )
        draft = draft_reminder(
            DraftInputs(
                merchant_name="Vasooli Demo",
                customer_name=customer.name,
                invoice_number=invoice.invoice_number,
                outstanding_paise=invoice.outstanding_paise,
                due_date=invoice.due_at.date().isoformat(),
                days_overdue=invoice.days_overdue,
                payment_url=(f"https://rzp.io/rzp/baseline-{invoice.invoice_number.lower()}"),
                reason_explanation=diagnosis.explanation,
                tier=tier,
            ),
            use_llm=False,
        )
        decision = evaluate_reminder(
            invoice_number=invoice.invoice_number,
            status=invoice.status,
            reason_category=diagnosis.category,
            has_prior_dispute_note=invoice.has_prior_dispute_note,
            outstanding_paise=invoice.outstanding_paise,
            days_overdue=invoice.days_overdue,
            reminders_sent=invoice.reminders_sent,
            sent_tiers=frozenset(),
            last_reminder_at=invoice.last_reminder_at,
            active_promise_date=None,
            proposed_tier=tier,
            drafted_subject=draft.subject,
            drafted_body=draft.body,
            now=captured_at,
        )
        traces.append(
            {
                "invoice_number": invoice.invoice_number,
                "days_overdue": invoice.days_overdue,
                "diagnosis": diagnosis.category.value,
                "draft_tone": draft.tone,
                "decision": decision.to_dict(),
            }
        )

    return {"trace_count": len(traces), "traces": traces}


def _response(response) -> dict[str, Any]:
    assert response.status_code < 400, f"{response.request.url}: {response.text}"
    content_type = response.headers.get("content-type", "")
    body: Any = response.json() if "json" in content_type else response.text
    return {
        "status": response.status_code,
        "content_type": content_type.split(";")[0],
        "body": body,
    }


def capture_api_responses(
    client: TestClient, session: Session, headers: Mapping[str, str]
) -> dict[str, Any]:
    """Snapshot every read contract consumed by the current demo screens."""

    invoices = list(session.exec(select(Invoice).order_by(Invoice.invoice_number)).all())
    endpoints: dict[str, Any] = {}

    public_paths = ("/health", "/api/auth/modes")
    read_paths = (
        "/api/dashboard/runtime",
        "/api/dashboard/automation",
        "/api/dashboard/overview?days=30",
        "/api/dashboard/queue?limit=200",
        "/api/dashboard/promises",
        "/api/dashboard/audit?limit=200",
        "/api/dashboard/exceptions",
        "/api/dashboard/disputes",
        "/api/payments/methods",
        "/api/invoices?limit=50",
        "/api/invoices/import/template",
        "/api/export/invoices?format=csv",
        "/api/export/recovered?format=csv",
        "/api/export/overview?format=csv",
        "/api/demo/clock",
    )

    for path in public_paths:
        endpoints[f"GET {path}"] = _response(client.get(path))
    for path in read_paths:
        endpoints[f"GET {path}"] = _response(client.get(path, headers=dict(headers)))

    for invoice in invoices:
        number = invoice.invoice_number
        for label, path in (
            ("invoice", f"/api/invoices/{invoice.id}"),
            ("detail", f"/api/dashboard/invoices/{invoice.id}"),
            ("payments", f"/api/dashboard/invoices/{invoice.id}/payments"),
        ):
            endpoints[f"GET {label} {number}"] = _response(client.get(path, headers=dict(headers)))

    # The reviewer settings panel is part of the frozen experience too. These calls
    # are sequenced after all read snapshots so their audited state changes cannot
    # perturb the dashboard fixtures above. No email is sent and no recovery cycle is
    # run while capturing them.
    settings_actions = (
        (
            "POST /api/demo/email-redirect set",
            "/api/demo/email-redirect",
            {"address": "baseline-reviewer@example.com"},
        ),
        (
            "POST /api/demo/advance",
            "/api/demo/advance",
            {"days": 1, "run_cycle": False, "dry_run": True},
        ),
        ("POST /api/demo/reset", "/api/demo/reset", None),
        (
            "POST /api/demo/email-redirect clear",
            "/api/demo/email-redirect",
            {"address": None},
        ),
    )
    for label, path, payload in settings_actions:
        endpoints[label] = _response(
            client.post(path, headers=dict(headers), json=payload)
            if payload is not None
            else client.post(path, headers=dict(headers))
        )

    return normalize({"endpoint_count": len(endpoints), "endpoints": endpoints})


def normalize(value: Any) -> Any:
    """Remove only values that cannot be stable across clean database runs."""

    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, datetime):
        return "<datetime>"
    if not isinstance(value, str):
        return value

    text = _UUID.sub("<uuid>", value)
    text = _ISO_DATETIME.sub("<datetime>", text)
    text = _ISO_DATE.sub("<date>", text)
    text = _DISPLAY_DATETIME.sub("<display-datetime>", text)
    return text


def pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
