"""Audit records for AI behaviour. P1.

AI failures were previously only application logs — invisible to anyone looking at an
invoice, and gone once the log rotated. A merchant reading "this reminder was written
by a template, not the model" on the timeline is the difference between a system that
degraded gracefully and one that looks like it just wrote worse copy for no reason.

**What is deliberately NOT stored:** prompts, customer reply text, API keys, and model
output beyond what is already persisted on the reminder. An audit trail that quietly
becomes a second copy of every customer message is a privacy problem, and one nobody
remembers is there.
"""

import uuid

from sqlmodel import Session

from app.ai.client import LLMResult
from app.models import AuditAction, AuditActor, AuditLog


def record_llm_outcome(
    session: Session,
    *,
    invoice_id: uuid.UUID | None,
    task: str,
    result: LLMResult,
    fell_back: bool,
) -> None:
    """Record how an AI call went, without recording what was said.

    Three distinct outcomes, because they mean different things operationally:

    * failover  — the primary was unavailable, a fallback answered. Degraded, working.
    * unavailable — no model answered; deterministic code produced the result.
    * fine — the primary answered; nothing worth an audit row.
    """
    if result.ok and not result.degraded:
        return  # the normal case needs no record

    if result.failed:
        action = AuditAction.LLM_UNAVAILABLE
        detail = {
            "task": task,
            "models_attempted": list(result.attempts),
            # Truncated, and never the prompt or the reply.
            "error": (result.error or "unknown")[:200],
            "fell_back_to_deterministic": fell_back,
        }
    else:
        action = AuditAction.LLM_FAILOVER
        detail = {
            "task": task,
            "answered_by": result.model,
            "models_attempted": list(result.attempts),
        }

    session.add(
        AuditLog(
            invoice_id=invoice_id,
            actor=AuditActor.AI,
            action=action,
            detail=detail,
        )
    )


def record_output_rejected(
    session: Session, *, invoice_id: uuid.UUID | None, task: str, reason: str, model: str | None
) -> None:
    """The model answered, and we refused its answer.

    Worth auditing separately from an outage: a model inventing an amount is a
    different problem from a model being down, and only one of them means the prompt
    or the schema needs work.
    """
    session.add(
        AuditLog(
            invoice_id=invoice_id,
            actor=AuditActor.AI,
            action=AuditAction.LLM_OUTPUT_REJECTED,
            detail={"task": task, "reason": reason, "model": model},
        )
    )
