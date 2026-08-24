"""Provenance records for AI operations. P1.

Answers, from the audit trail alone, the questions a reviewer will ask about any
AI-influenced decision:

    Which model answered?          → `model`
    Was failover triggered?        → `models_attempted`, `degraded`
    Did the output fail schema?    → action = llm_output_rejected, `reason`
    Did deterministic code decide? → action = deterministic_fallback
    What operation was this?       → `task`
    Was the result accepted?       → `accepted`
    Why not?                       → `reason`

**What is deliberately NOT stored:** prompts, customer reply text, drafted message
bodies, and API keys. An audit trail that quietly becomes a second copy of every
customer message is a privacy problem — and one nobody remembers is there. The drafted
message is already persisted on the reminder, where it belongs; duplicating it here
would mean two places to redact.
"""

import uuid
from enum import StrEnum

from sqlmodel import Session

from app.models import AuditAction, AuditActor, AuditLog


class AITask(StrEnum):
    """Which AI operation this record is about."""

    DIAGNOSE = "diagnose"
    DRAFT_REMINDER = "draft_reminder"
    EXTRACT_PROMISE = "extract_promise"
    ANALYSE_DISPUTE = "analyse_dispute"


def record_ai_outcome(
    session: Session,
    *,
    invoice_id: uuid.UUID | None,
    task: AITask | str,
    model: str | None,
    models_attempted: tuple[str, ...] | list[str] = (),
    accepted: bool,
    used_fallback: bool,
    reason: str | None = None,
    error: str | None = None,
) -> None:
    """Record one AI operation's provenance and outcome.

    Emits at most one row, and only when there is something to say. A primary model
    answering normally and being accepted is the expected case; a row for every such
    call would bury the interesting ones.
    """
    degraded = used_fallback or (bool(models_attempted) and len(models_attempted) > 1)

    if accepted and not degraded:
        return

    if not accepted and used_fallback and model is None:
        action = AuditAction.LLM_UNAVAILABLE
    elif not accepted:
        action = AuditAction.LLM_OUTPUT_REJECTED
    elif used_fallback:
        action = AuditAction.DETERMINISTIC_FALLBACK
    else:
        action = AuditAction.LLM_FAILOVER

    detail: dict[str, object] = {
        "task": str(task),
        "model": model,
        "models_attempted": list(models_attempted),
        "accepted": accepted,
        "deterministic_fallback": used_fallback,
    }
    if reason:
        detail["reason"] = reason
    if error:
        # Truncated, and never the prompt or the model's text.
        detail["error"] = error[:200]

    session.add(
        AuditLog(
            invoice_id=invoice_id,
            actor=AuditActor.AI,
            action=action,
            detail=detail,
        )
    )
