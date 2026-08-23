"""Reminder copy. Doc §3 Stage 3.

Three layers of protection sit between the model and a customer's inbox:

1. The prompt states the compliance rules and the exact figures to use.
2. `verify_figures` checks that the amount, invoice number, and link in the draft are
   the ones we supplied. A model that invents a digit in a payment amount is a money
   bug, not a style problem.
3. app.policy runs the banned-language check on the finished text, independently of
   anything the prompt asked for.

Only the third is trusted. The first two reduce how often it has to fire.
"""

from dataclasses import dataclass

from app.ai.client import LLMClient, get_llm_client
from app.ai.prompts.draft_reminder import DRAFT_PROMPT, TONE_GUIDANCE
from app.ai.schemas import DraftResponse
from app.core.constants import TONE_FOR_TIER
from app.core.logging import get_logger
from app.core.money import paise_to_rupees

log = get_logger("ai.drafting")


@dataclass(frozen=True)
class DraftInputs:
    merchant_name: str
    customer_name: str
    invoice_number: str
    outstanding_paise: int
    due_date: str
    days_overdue: int
    payment_url: str
    reason_explanation: str
    tier: int


@dataclass(frozen=True)
class Draft:
    subject: str
    body: str
    tone: str
    tone_rationale: str
    #: Model id, or "template_fallback".
    generated_by: str
    degraded: bool = False


def _amount_text(paise: int) -> str:
    """How the amount must appear in the message.

    Formatted once and passed to both the prompt and the verifier, so the string the
    model is told to use is byte-identical to the one we check for.
    """
    rupees = paise_to_rupees(paise)
    whole = int(rupees)
    return f"{whole:,}" if rupees == whole else f"{rupees:,}"


def verify_figures(text: str, inputs: DraftInputs) -> list[str]:
    """Facts that should be in the draft but are not.

    A missing figure means the model paraphrased or invented one. Either way the draft
    is discarded in favour of the template — the alternative is emailing a customer an
    amount that does not match their invoice.
    """
    problems = []
    if _amount_text(inputs.outstanding_paise) not in text:
        problems.append("amount")
    if inputs.invoice_number not in text:
        problems.append("invoice_number")
    if inputs.payment_url and inputs.payment_url not in text:
        problems.append("payment_url")
    return problems


# --------------------------------------------------------------------------
# Deterministic fallback. Ships in the repo, always passes policy, no model needed.
# --------------------------------------------------------------------------

_TEMPLATES = {
    1: (
        "Invoice {invoice_number} — friendly reminder",
        "Hello {customer_name},\n\n"
        "This is a gentle reminder that invoice {invoice_number} for Rs {amount} "
        "was due on {due_date} and is showing as unpaid.\n\n"
        "If it has already been sent, please ignore this note. Otherwise you can "
        "settle it here:\n{payment_url}\n\n"
        "Thanks very much,\n{merchant_name}",
    ),
    2: (
        "Invoice {invoice_number} — payment overdue",
        "Hello {customer_name},\n\n"
        "Invoice {invoice_number} for Rs {amount} was due on {due_date} and is now "
        "{days_overdue} days overdue.\n\n"
        "Could you confirm when we can expect payment, or let us know if something "
        "is holding it up? You can pay here:\n{payment_url}\n\n"
        "Thanks,\n{merchant_name}",
    ),
    3: (
        "Invoice {invoice_number} — final reminder",
        "Hello {customer_name},\n\n"
        "Invoice {invoice_number} for Rs {amount} is now {days_overdue} days overdue "
        "and remains unpaid despite our earlier messages.\n\n"
        "This is the last automated reminder we will send. A colleague will follow up "
        "with you directly. If payment has already been made, please let us know so we "
        "can update our records.\n\nPayment link:\n{payment_url}\n\n"
        "Regards,\n{merchant_name}",
    ),
}


def template_draft(inputs: DraftInputs) -> Draft:
    """The message sent when no model is available.

    Deliberately plain. It is accurate, compliant by construction, and contains every
    figure a customer needs — which is most of what a reminder has to do.
    """
    subject_tpl, body_tpl = _TEMPLATES[inputs.tier]
    fields = {
        "invoice_number": inputs.invoice_number,
        "customer_name": inputs.customer_name,
        "merchant_name": inputs.merchant_name,
        "amount": _amount_text(inputs.outstanding_paise),
        "due_date": inputs.due_date,
        "days_overdue": inputs.days_overdue,
        "payment_url": inputs.payment_url,
    }
    return Draft(
        subject=subject_tpl.format(**fields),
        body=body_tpl.format(**fields),
        tone=TONE_FOR_TIER[inputs.tier].value,
        tone_rationale="Deterministic template — no model was available.",
        generated_by="template_fallback",
        degraded=True,
    )


def draft_reminder(
    inputs: DraftInputs,
    *,
    client: LLMClient | None = None,
    use_llm: bool = True,
    banned_phrases: list[str] | None = None,
) -> Draft:
    """Draft one reminder.

    `banned_phrases` is set on a regeneration attempt: policy rejected the previous
    draft and named what was wrong, so the model gets one chance to fix it. Never more
    than one — Doc §5 is explicit that the rules layer is independent of what the model
    drafts, and a third attempt would be trusting the model to eventually behave.
    """
    if not use_llm:
        return template_draft(inputs)

    client = client or get_llm_client()
    tone = TONE_FOR_TIER[inputs.tier].value

    prompt = DRAFT_PROMPT.format(
        merchant_name=inputs.merchant_name,
        customer_name=inputs.customer_name,
        invoice_number=inputs.invoice_number,
        outstanding_inr=_amount_text(inputs.outstanding_paise),
        due_date=inputs.due_date,
        days_overdue=inputs.days_overdue,
        payment_url=inputs.payment_url,
        reason_explanation=inputs.reason_explanation,
        tone=tone,
        tone_guidance=TONE_GUIDANCE[tone],
    )
    if banned_phrases:
        prompt += (
            "\nYour previous draft was rejected for containing: "
            f"{', '.join(banned_phrases)}.\n"
            "Rewrite it without those phrases or anything similar in meaning.\n"
        )

    result = client.generate_structured(
        prompt=prompt,
        response_model=DraftResponse,
        task="draft_reminder",
        invoice_number=inputs.invoice_number,
    )

    if not result.ok or result.value is None:
        return template_draft(inputs)

    value = result.value
    missing = verify_figures(f"{value.subject}\n{value.body}", inputs)
    if missing:
        log.warning(
            "drafting.figures_missing",
            invoice_number=inputs.invoice_number,
            missing=missing,
            model=result.model,
        )
        return template_draft(inputs)

    return Draft(
        subject=value.subject,
        body=value.body,
        tone=tone,
        tone_rationale=value.tone_rationale,
        generated_by=result.model or "template_fallback",
        degraded=result.degraded,
    )
