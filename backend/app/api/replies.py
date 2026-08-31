"""Customer reply endpoints. Doc §3 Stage 4.

`simulate-reply` is the demo's path and is deliberately not a shortcut: it runs the
same extraction, the same validation, and the same promise-pausing logic that a real
inbound email would. Live inbound parsing depends on a mail provider feature and a
verified domain; this does not, so the promise loop is demonstrable either way.
"""

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import OperatorRequired
from app.core.config import settings
from app.core.db import SessionDep
from app.models import Invoice
from app.services.demo_scope import is_demo_invoice
from app.services.replies import handle_reply

router = APIRouter(prefix="/api", tags=["replies"], dependencies=[OperatorRequired])


class SimulatedReply(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    #: Set false to force the regex extractor, for testing the no-model path.
    use_llm: bool = True


class ReplyResponse(BaseModel):
    invoice_number: str
    promise_created: bool
    escalated: bool
    is_complaint: bool
    promised_date: str | None
    confidence: float
    note: str
    #: Present when the reply opened or matched a dispute case.
    dispute_case_id: str | None = None


@router.post("/invoices/{invoice_id}/simulate-reply", response_model=ReplyResponse)
def simulate_reply(
    invoice_id: uuid.UUID, payload: SimulatedReply, session: SessionDep
) -> ReplyResponse:
    """Feed a customer reply into the system as though it had arrived by email."""
    # Off unless explicitly enabled. The real path is
    # POST /api/webhooks/resend/inbound, which requires a Svix signature and
    # correlates the sender against the invoice thread. This endpoint requires
    # neither, so leaving it reachable in production would mean anyone with the
    # admin key could fabricate customer statements into the audit trail.
    if not settings.allow_simulated_replies:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Simulated replies are disabled. Customer replies arrive by email at "
            "invoice-<number>@<EMAIL_REPLY_TO_DOMAIN> and are processed by "
            "/api/webhooks/resend/inbound.",
        )

    # A fabricated customer statement must never be injectable into a real merchant's
    # ledger. The flag alone is not the boundary: it is a deployment setting, and a
    # demo deployment that turns it on is exactly the one live merchants also sign up
    # to. Scope, not configuration, is what keeps this on the demo side.
    invoice = session.get(Invoice, invoice_id)
    if not is_demo_invoice(session, invoice):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")

    outcome = handle_reply(session, invoice, payload.body, use_llm=payload.use_llm)
    return ReplyResponse(**outcome.__dict__)
