"""Structured outputs the model must return. Doc §3 Stage 2, Stage 4.

Every LLM call is schema-constrained and validated. Free-text output would have to be
parsed, and a parser that guesses is another place for the model to be quietly wrong
about money.

Field constraints here are load-bearing, not decoration: a confidence outside 0-1 or a
promised date two years out is a malformed answer, and rejecting it triggers the
repair-then-fallback chain rather than writing nonsense to the database.
"""

from datetime import date

from pydantic import BaseModel, Field

from app.core.constants import ReasonCategory


class DiagnosisResponse(BaseModel):
    """Why this invoice is likely at risk. Doc §3 Stage 2.

    The model proposes a category, but the rule-based classifier is authoritative —
    the four categories are *defined* as rules over customer history, so a model that
    disagrees is wrong by construction. What the model genuinely adds is the
    plain-language explanation, which is what a human reads on the dashboard.
    """

    category: ReasonCategory
    explanation: str = Field(
        max_length=280,
        description="One or two plain sentences a merchant could read aloud to a colleague.",
    )
    confidence: float = Field(ge=0, le=1)
    signals_used: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="Which customer-history facts drove this, for the audit trail.",
    )


class DraftResponse(BaseModel):
    """A drafted reminder. Doc §3 Stage 3."""

    subject: str = Field(max_length=120)
    body: str = Field(max_length=2000)
    tone_rationale: str = Field(
        max_length=200, description="Why this tone suits this customer and tier."
    )


class PromiseExtraction(BaseModel):
    """What a customer's reply commits to. Doc §3 Stage 4."""

    has_promise: bool
    promised_date: date | None = None
    #: A string, not a float. Money never travels as a float in this codebase, and a
    #: model's JSON number would already have lost precision before app.core.money
    #: could refuse it. Parsed exactly via rupees_to_paise.
    promised_amount_inr: str | None = Field(
        default=None,
        description='Rupee amount as digits only, e.g. "25000" or "25000.50". '
        "Null when the customer named a date but no amount — that means the full "
        "outstanding balance.",
    )
    confidence: float = Field(ge=0, le=1, default=0.0)
    excerpt: str = Field(
        default="", max_length=300, description="The words that constitute the promise."
    )
    #: Routes the invoice to DISPUTE_LIKELY and out of the automated cadence entirely.
    #: A complaint is not a payment negotiation and must not be answered by a nudge.
    is_complaint: bool = False
