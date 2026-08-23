"""Why an invoice is likely at risk. Doc §3 Stage 2.

The four categories are *definitions*, not predictions — each one is a statement about
the customer's payment history that is either true or false. So the classifier is
deterministic Python, and the model's contribution is the plain-language explanation a
human reads on the dashboard.

That ordering is what makes the whole AI layer optional rather than critical. If both
models are unavailable, diagnosis still works; it just reads more mechanically.
"""

from dataclasses import dataclass

from app.ai.client import LLMClient, get_llm_client
from app.ai.prompts.diagnose import DIAGNOSE_PROMPT
from app.ai.schemas import DiagnosisResponse
from app.core.constants import ReasonCategory
from app.core.logging import get_logger
from app.core.money import paise_to_rupees

log = get_logger("ai.diagnosis")


@dataclass(frozen=True)
class DiagnosisInputs:
    """Exactly the signals Doc §3 Stage 2 permits. Nothing else is in scope."""

    total_invoices: int
    invoices_paid_late: int
    invoices_defaulted: int
    broken_promises: int
    avg_invoice_paise: int
    amount_paise: int
    days_overdue: int
    has_prior_dispute_note: bool
    has_reply: bool
    reply_has_complaint: bool
    current_tier: int


@dataclass(frozen=True)
class Diagnosis:
    category: ReasonCategory
    explanation: str
    confidence: float
    signals_used: tuple[str, ...]
    #: Which model wrote the explanation, or "rule_based" when none did.
    source: str
    #: True when the model proposed a different category. The rule still wins; this is
    #: recorded because it is a reported eval metric in Phase 11, and because a
    #: persistent disagreement means one of the two is miscalibrated.
    llm_disagreed: bool = False


def rule_based_diagnosis(inputs: DiagnosisInputs) -> ReasonCategory:
    """The authoritative classifier. Doc §3 Stage 2, read literally.

    Precedence is fixed and matters: a customer can be both "has defaulted before" and
    "is disputing this invoice", and the dispute has to win, because the correct next
    action is a human conversation rather than any kind of chase.
    """
    if inputs.has_prior_dispute_note or inputs.reply_has_complaint:
        return ReasonCategory.DISPUTE_LIKELY

    # "No reply received after the Tier 2 reminder was sent."
    if inputs.current_tier >= 2 and not inputs.has_reply:
        return ReasonCategory.UNRESPONSIVE

    # Defaulted before means they do not reliably pay at all — not a cash-flow gap.
    if inputs.invoices_defaulted > 0:
        return ReasonCategory.UNRESPONSIVE

    # "Clean payment history and first time overdue."
    if inputs.invoices_paid_late == 0:
        return ReasonCategory.OVERSIGHT

    # "Paid late before, but has always eventually paid in full."
    return ReasonCategory.CASH_CONSTRAINED


def _rule_explanation(category: ReasonCategory, inputs: DiagnosisInputs) -> str:
    """Deterministic fallback copy. Plain, accurate, unglamorous."""
    match category:
        case ReasonCategory.OVERSIGHT:
            return (
                f"This customer has paid all {inputs.total_invoices} previous invoices "
                "on time. A first-time delay like this is usually an oversight."
            )
        case ReasonCategory.CASH_CONSTRAINED:
            ratio = (
                inputs.amount_paise / inputs.avg_invoice_paise if inputs.avg_invoice_paise else 1.0
            )
            size_note = (
                f" This invoice is about {ratio:.1f} times their usual size."
                if ratio >= 1.5
                else ""
            )
            return (
                f"This customer has paid late {inputs.invoices_paid_late} times before "
                f"but has always paid in the end.{size_note}"
            )
        case ReasonCategory.DISPUTE_LIKELY:
            reason = (
                "they have raised a complaint"
                if inputs.reply_has_complaint
                else "there is a dispute note on this invoice"
            )
            return f"Payment appears to be held up because {reason}, not because of cash flow."
        case _:
            return (
                f"This customer has not responded and has left "
                f"{inputs.invoices_defaulted} invoice(s) unpaid in the past."
            )


def _signals(inputs: DiagnosisInputs) -> tuple[str, ...]:
    signals = [
        f"total_invoices={inputs.total_invoices}",
        f"paid_late={inputs.invoices_paid_late}",
        f"defaulted={inputs.invoices_defaulted}",
        f"days_overdue={inputs.days_overdue}",
    ]
    if inputs.broken_promises:
        signals.append(f"broken_promises={inputs.broken_promises}")
    if inputs.has_prior_dispute_note:
        signals.append("prior_dispute_note")
    return tuple(signals)


def diagnose(
    inputs: DiagnosisInputs,
    *,
    invoice_number: str | None = None,
    client: LLMClient | None = None,
    use_llm: bool = True,
) -> Diagnosis:
    """Classify by rule, then ask a model to explain it."""
    category = rule_based_diagnosis(inputs)
    signals = _signals(inputs)

    if not use_llm:
        return Diagnosis(
            category=category,
            explanation=_rule_explanation(category, inputs),
            confidence=1.0,
            signals_used=signals,
            source="rule_based",
        )

    client = client or get_llm_client()
    prompt = DIAGNOSE_PROMPT.format(
        rule_category=category.value,
        total_invoices=inputs.total_invoices,
        invoices_paid_late=inputs.invoices_paid_late,
        invoices_defaulted=inputs.invoices_defaulted,
        broken_promises=inputs.broken_promises,
        avg_invoice_inr=paise_to_rupees(inputs.avg_invoice_paise),
        amount_inr=paise_to_rupees(inputs.amount_paise),
        days_overdue=inputs.days_overdue,
        has_prior_dispute_note="yes" if inputs.has_prior_dispute_note else "no",
        has_reply="yes" if inputs.has_reply else "no",
    )

    result = client.generate_structured(
        prompt=prompt,
        response_model=DiagnosisResponse,
        task="diagnose",
        invoice_number=invoice_number,
    )

    if not result.ok or result.value is None:
        return Diagnosis(
            category=category,
            explanation=_rule_explanation(category, inputs),
            confidence=1.0,
            signals_used=signals,
            source="rule_based",
        )

    proposed = result.value
    disagreed = proposed.category is not category
    if disagreed:
        # The rule wins. The categories are definitions over history, so a model that
        # disagrees is disagreeing with arithmetic.
        log.info(
            "diagnosis.llm_disagreed",
            invoice_number=invoice_number,
            rule=category.value,
            llm=proposed.category.value,
        )

    return Diagnosis(
        category=category,
        explanation=proposed.explanation,
        confidence=proposed.confidence,
        signals_used=tuple(proposed.signals_used) or signals,
        source=result.model or "rule_based",
        llm_disagreed=disagreed,
    )
