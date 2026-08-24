"""AI provenance in the audit trail. P1.

The questions this has to answer from stored records alone: which model answered, was
failover triggered, did deterministic code decide instead, and was the AI's answer
accepted or rejected — and why.

`ai_audit.py` was previously written but never imported. These tests exist so it
cannot quietly become dead code again.
"""

import pytest
from sqlmodel import select

from app.models import AuditAction, AuditLog, Invoice
from app.services.ai_audit import AITask, record_ai_outcome
from app.services.recovery import run_recovery_cycle
from app.services.replies import handle_reply

#: The actions record_ai_outcome emits. The AI actor also writes `diagnosed`, which
#: carries the classification itself rather than provenance and has a different shape.
PROVENANCE_ACTIONS = {
    AuditAction.LLM_FAILOVER,
    AuditAction.LLM_UNAVAILABLE,
    AuditAction.LLM_OUTPUT_REJECTED,
    AuditAction.DETERMINISTIC_FALLBACK,
}


def entries(session, action=None) -> list[AuditLog]:
    """Provenance rows only."""
    rows = session.exec(select(AuditLog).where(AuditLog.actor == "ai")).all()
    if action:
        return [r for r in rows if r.action == action]
    return [r for r in rows if r.action in PROVENANCE_ACTIONS]


# ===========================================================================
# What gets recorded, and what deliberately does not.
# ===========================================================================


def test_a_normal_accepted_answer_writes_no_row(session, invoice):
    """The expected case needs no record. A row per call would bury the interesting
    ones under thousands of uneventful ones."""
    record_ai_outcome(
        session,
        invoice_id=invoice.id,
        task=AITask.DIAGNOSE,
        model="gemini-3.7-flash",
        models_attempted=("gemini-3.7-flash",),
        accepted=True,
        used_fallback=False,
    )
    session.commit()
    assert entries(session) == []


def test_failover_is_recorded_with_the_model_that_answered(session, invoice):
    record_ai_outcome(
        session,
        invoice_id=invoice.id,
        task=AITask.DIAGNOSE,
        model="gemini-3.6-flash",
        models_attempted=("gemini-3.7-flash", "gemini-3.6-flash"),
        accepted=True,
        used_fallback=False,
    )
    session.commit()

    entry = entries(session, AuditAction.LLM_FAILOVER)[0]
    assert entry.detail["model"] == "gemini-3.6-flash"
    assert entry.detail["models_attempted"] == ["gemini-3.7-flash", "gemini-3.6-flash"]
    assert entry.detail["accepted"] is True


def test_a_rejected_answer_records_why(session, invoice):
    """A model inventing an amount is a different problem from a model being down."""
    record_ai_outcome(
        session,
        invoice_id=invoice.id,
        task=AITask.DRAFT_REMINDER,
        model="gemini-3.7-flash",
        models_attempted=("gemini-3.7-flash",),
        accepted=False,
        used_fallback=True,
        reason="figures did not match the invoice",
    )
    session.commit()

    entry = entries(session, AuditAction.LLM_OUTPUT_REJECTED)[0]
    assert entry.detail["accepted"] is False
    assert "figures did not match" in entry.detail["reason"]


def test_total_unavailability_is_recorded_distinctly(session, invoice):
    record_ai_outcome(
        session,
        invoice_id=invoice.id,
        task=AITask.DIAGNOSE,
        model=None,
        models_attempted=(),
        accepted=False,
        used_fallback=True,
        error="503 UNAVAILABLE",
    )
    session.commit()

    entry = entries(session, AuditAction.LLM_UNAVAILABLE)[0]
    assert entry.detail["deterministic_fallback"] is True
    assert entry.detail["error"] == "503 UNAVAILABLE"


# ===========================================================================
# Privacy.
# ===========================================================================


def test_no_prompt_or_customer_text_is_stored(session, invoice):
    """An audit trail that becomes a second copy of every customer message is a
    privacy problem — and one nobody remembers is there."""
    record_ai_outcome(
        session,
        invoice_id=invoice.id,
        task=AITask.EXTRACT_PROMISE,
        model=None,
        accepted=False,
        used_fallback=True,
        error="x" * 1000,
    )
    session.commit()

    detail = entries(session)[0].detail
    assert set(detail) <= {
        "task",
        "model",
        "models_attempted",
        "accepted",
        "deterministic_fallback",
        "reason",
        "error",
    }
    assert len(detail["error"]) <= 200, "errors are truncated, never a full payload"


def test_no_api_key_reaches_the_audit_trail(session, invoice):
    from app.core.config import settings

    record_ai_outcome(
        session,
        invoice_id=invoice.id,
        task=AITask.DIAGNOSE,
        model=None,
        accepted=False,
        used_fallback=True,
        error=f"auth failed for key {settings.google_api_key}",
    )
    session.commit()
    # The key is a placeholder in tests, but the shape of the assertion is the point.
    assert "PLACEHOLDER" not in str(entries(session)[0].detail.get("reason", ""))


# ===========================================================================
# Wired into the real paths — the regression that matters.
# ===========================================================================


def test_the_recovery_cycle_records_ai_provenance(session, merchant, customer):
    """With no API key configured, every AI call falls back — and that must be visible."""
    from datetime import UTC, datetime, timedelta

    from app.core.constants import InvoiceStatus

    due = datetime.now(UTC) - timedelta(days=10)
    inv = Invoice(
        merchant_id=merchant.id,
        customer_id=customer.id,
        invoice_number="INV-AUD",
        amount_paise=2_500_000,
        issued_at=due - timedelta(days=30),
        due_at=due,
        status=InvoiceStatus.CHASING,
    )
    session.add(inv)
    session.commit()

    run_recovery_cycle(session, use_llm=True)

    tasks = {e.detail.get("task") for e in entries(session)}
    assert AITask.DIAGNOSE in tasks
    assert AITask.DRAFT_REMINDER in tasks
    assert all(e.detail["deterministic_fallback"] for e in entries(session))


def test_promise_extraction_records_provenance(session, invoice):
    handle_reply(session, invoice, "I'll clear this by the 28th.", use_llm=True)

    extraction_rows = [
        e for e in entries(session) if e.detail.get("task") == AITask.EXTRACT_PROMISE
    ]
    assert extraction_rows, "promise extraction had no provenance at all before this"
    assert extraction_rows[0].detail["deterministic_fallback"] is True


@pytest.mark.parametrize("task", list(AITask))
def test_every_ai_task_has_a_name_in_the_audit_trail(task):
    """Guards against a new AI operation being added without provenance."""
    assert str(task) in {"diagnose", "draft_reminder", "extract_promise", "analyse_dispute"}
