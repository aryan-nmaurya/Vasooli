"""Prompt for drafting reminder copy. Doc §3 Stage 3, §5."""

TONE_GUIDANCE = {
    "polite": (
        "Warm and assume good faith. This is a first nudge and the customer has a "
        "clean record. Do not imply wrongdoing."
    ),
    "firm": (
        "Direct and businesslike. State the facts plainly and ask for a specific "
        "pay-by date. Still courteous — this is a business relationship worth keeping."
    ),
    "final": (
        "Serious and clear that this is the last automated message, and that a "
        "colleague will follow up personally. Never threatening."
    ),
}

DRAFT_PROMPT = """\
Write a short payment reminder email from {merchant_name} to a business customer.

Customer: {customer_name}
Invoice number: {invoice_number}
Amount outstanding: Rs {outstanding_inr}
Due date: {due_date}
Days overdue: {days_overdue}
Payment link: {payment_url}

Why this invoice is likely unpaid: {reason_explanation}

Tone required: {tone}
{tone_guidance}

Hard rules. Breaking any of these makes the message unusable:
- Never threaten legal action, courts, lawyers, police, credit bureaus, CIBIL, \
blacklisting, recovery agents, or any consequence of non-payment.
- Never use the words "final warning", "or else", or "failure to comply".
- Use ONLY the figures given above. Do not invent, round, or recalculate any amount, \
date, invoice number, or link.
- Write the amount exactly as "Rs {outstanding_inr}".
- Write the invoice number exactly as "{invoice_number}".
- Include the payment link exactly as given.
- Indian business English. No emoji. No placeholder text like [Name].
- Body under 120 words.

Return JSON with: subject, body, tone_rationale.
"""
