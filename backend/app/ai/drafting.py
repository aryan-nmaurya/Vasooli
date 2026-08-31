"""Reminder copy. Doc §3 Stage 3.

Four layers of protection sit between the model and a customer's inbox:

1. The prompt states the compliance rules and the exact figures to use.
2. `verify_figures` checks that the amount, invoice number, and link we supplied are
   all PRESENT in the draft. A model that paraphrases the amount away has produced a
   reminder the customer cannot act on.
3. `find_invented_figures` checks that nothing else financial is present. This is the
   converse test and it is the one that matters more: a draft can contain every correct
   figure and *also* a second, invented one — "Rs 42,000 (Rs 4,20,000 including late
   fees)", a payment URL the model made up, a reference number that belongs to nobody.
   Presence checking passes that draft. The audit named this exact hole, and closing it
   is why every money-shaped token in the text is now matched against an allowlist
   rather than merely searched for.
4. app.policy runs the banned-language check on the finished text, independently of
   anything the prompt asked for.

Only the fourth is trusted for compliance. Layers 2 and 3 are what make the figures
safe, and they are deterministic — no model is asked to check another model's work.
"""

import re
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


# ---------------------------------------------------------------------------
# The converse check: nothing financial in the draft that we did not supply.
# ---------------------------------------------------------------------------

#: An amount, however the model chose to write it. Both the currency-prefixed form and
#: the bare grouped/decimal form, because "the balance of 4,20,000 is now due" is just
#: as wrong as "Rs 4,20,000" and neither is caught by looking for a rupee sign.
_MONEY = re.compile(
    r"(?:(?:₹|\bRs\.?|\bINR)\s*([\d][\d,]*(?:\.\d+)?)|\b(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?)\b)",
    re.IGNORECASE,
)

#: Any link. A reminder has exactly one legitimate URL and it is the one we minted.
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)

#: An invoice-number-shaped token: letters then digits, optionally hyphenated.
_REFERENCE = re.compile(r"\b[A-Z]{2,6}[-/]?\d{3,}\b")

#: A bare run of four or more digits — an unformatted amount, an account number, a
#: reference. Years are excluded below rather than here, because "2026" in a due date is
#: ordinary and rejecting it would discard every correct draft.
_LONG_NUMBER = re.compile(r"\b\d{4,}\b")

#: A small number sitting next to a money word. `_MONEY` needs a currency marker or
#: grouping, and `_LONG_NUMBER` needs four digits, so "a processing charge of 500
#: applies" passed both — a fee nobody agreed to, in a figure small enough to look
#: routine. Anchoring on the vocabulary of an added charge catches it without
#: rejecting "3 days" or "2 invoices".
_CHARGE_WORDS = r"fee|charge|penalty|interest|surcharge|levy|fine|commission|late\s*payment"
_SMALL_CHARGE = re.compile(
    rf"(?:{_CHARGE_WORDS})[^.\n]{{0,40}}?\b(\d[\d,]*(?:\.\d+)?)\b"
    rf"|\b(\d[\d,]*(?:\.\d+)?)\s*(?:%|percent)?[^.\n]{{0,20}}?(?:{_CHARGE_WORDS})",
    re.IGNORECASE,
)

#: A percentage. An interest or late-payment rate carries no rupee figure at all, so
#: none of the amount checks see it, and "2% per month" is a term the merchant never
#: agreed to being asserted to their customer.
# No trailing `\b`: between "%" and a space there is no word boundary, so anchoring
# one there silently matched nothing for the commonest form of all — "2%".
_PERCENTAGE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|per\s*cent\b|percent\b)", re.IGNORECASE)

#: Somewhere else to send the money. The prompt forbids an alternative payment address
#: and nothing enforced it: `_URL` matches only http(s):// and www., so an email
#: address, a UPI handle and a bare `pay-now.example/settle` all passed. This is the
#: highest-consequence miss of the three — it is the shape a redirected payment takes.
_ALT_DESTINATION = re.compile(
    # An email address or UPI handle: something@something, no scheme.
    r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\b"
    # A bare domain with a path and no scheme.
    r"|\b(?:[a-z0-9-]+\.)+[a-z]{2,}/\S+",
    re.IGNORECASE,
)


def _normalise_amount(raw: str) -> str:
    """Strip grouping so "4,20,000" and "420000" compare equal.

    Indian grouping (lakh/crore) and Western grouping produce different strings for the
    same number, and the model may reformat ours. Comparing the digits is the only
    stable test.
    """
    return raw.replace(",", "").rstrip("0").rstrip(".") if "." in raw else raw.replace(",", "")


def find_invented_figures(text: str, inputs: DraftInputs) -> list[str]:
    """Financial values in the draft that we did not supply.

    Deliberately an allowlist, not a blocklist. Enumerating the ways a model can be
    wrong about money is a losing game; enumerating the handful of values that are
    correct is finite and checkable.

    Returns short labels rather than the offending text, so the reason can be logged and
    audited without copying a possibly-fabricated amount into the trail as though it
    were real.
    """
    allowed_amounts = {
        _normalise_amount(_amount_text(inputs.outstanding_paise)),
        # The paise-exact form, in case the model writes "42000.00".
        _normalise_amount(f"{inputs.outstanding_paise / 100:.2f}"),
    }
    problems: list[str] = []

    for match in _MONEY.finditer(text):
        value = match.group(1) or match.group(2) or ""
        if _normalise_amount(value) not in allowed_amounts:
            problems.append("extra_amount")
            break

    for url in _URL.finditer(text):
        # Trailing punctuation is the model's, not part of the link.
        found = url.group(0).rstrip(".,);:")
        if found != inputs.payment_url:
            problems.append("extra_url")
            break

    for reference in _REFERENCE.finditer(text):
        if reference.group(0) != inputs.invoice_number:
            problems.append("extra_reference")
            break

    allowed_numbers = {
        _normalise_amount(_amount_text(inputs.outstanding_paise)),
        str(inputs.days_overdue),
    } | {token for token in re.findall(r"\d+", inputs.due_date)}
    for number in _LONG_NUMBER.finditer(text):
        token = number.group(0)
        if token in allowed_numbers:
            continue
        # A plausible calendar year is ordinary prose in a due-date sentence.
        if 1900 <= int(token) <= 2100 and len(token) == 4:
            continue
        # Part of the payment URL or the invoice number, both already checked above.
        if token in inputs.payment_url or token in inputs.invoice_number:
            continue
        problems.append("extra_number")
        break

    for match in _SMALL_CHARGE.finditer(text):
        value = match.group(1) or match.group(2) or ""
        if _normalise_amount(value) not in allowed_amounts:
            problems.append("extra_charge")
            break

    if _PERCENTAGE.search(text):
        problems.append("extra_rate")

    for match in _ALT_DESTINATION.finditer(text):
        found = match.group(0).rstrip(".,);:")
        # The payment link is checked by `_URL` above and legitimately contains a host.
        if found in inputs.payment_url:
            continue
        problems.append("alternative_payment_destination")
        break

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
        "If it has already been sent, please ignore this note.{payment_block}\n\n"
        "Thanks very much,\n{merchant_name}",
    ),
    2: (
        "Invoice {invoice_number} — payment overdue",
        "Hello {customer_name},\n\n"
        "Invoice {invoice_number} for Rs {amount} was due on {due_date} and is now "
        "{days_overdue} days overdue.\n\n"
        "Could you confirm when we can expect payment, or let us know if something "
        "is holding it up?{payment_block}\n\n"
        "Thanks,\n{merchant_name}",
    ),
    3: (
        "Invoice {invoice_number} — final reminder",
        "Hello {customer_name},\n\n"
        "Invoice {invoice_number} for Rs {amount} is now {days_overdue} days overdue "
        "and remains unpaid despite our earlier messages.\n\n"
        "This is the last automated reminder we will send. A colleague will follow up "
        "with you directly. If payment has already been made, please let us know so we "
        "can update our records.{payment_block}\n\n"
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
        # Omitted entirely when provisioning has not run, rather than leaving a
        # dangling "Payment link:" with nothing after it — which is what a customer
        # would see if the label were unconditional.
        "payment_block": (
            f"\n\nYou can pay here:\n{inputs.payment_url}" if inputs.payment_url else ""
        ),
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
    text = f"{value.subject}\n{value.body}"

    missing = verify_figures(text, inputs)
    if missing:
        log.warning(
            "drafting.figures_missing",
            invoice_number=inputs.invoice_number,
            missing=missing,
            model=result.model,
        )
        return template_draft(inputs)

    invented = find_invented_figures(text, inputs)
    if invented:
        # Every required figure was present AND something else financial was too. This
        # is the draft that looks correct on a skim: the right amount, the right link,
        # plus a late fee nobody agreed to. The template is used instead.
        log.warning(
            "drafting.figures_invented",
            invoice_number=inputs.invoice_number,
            problems=invented,
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
