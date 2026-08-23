"""Prompt for reason diagnosis. Doc §3 Stage 2."""

DIAGNOSE_PROMPT = """\
You are helping a business understand why one of its invoices is unpaid.

The four categories are defined by strict rules, not judgment:

- oversight: the customer has NEVER paid late before, and this is their first overdue \
invoice. A clean payer who simply missed one.
- cash_constrained: the customer HAS paid late before, but has ALWAYS eventually paid \
in full. Never defaulted.
- dispute_likely: the customer has complained, or this invoice has a prior dispute note.
- unresponsive: the customer has defaulted before, or has not replied after a firm \
reminder.

A rules engine has already applied these definitions and decided the category is:
{rule_category}

Your job is NOT to second-guess that. Your job is to explain it in plain language a \
business owner could read aloud, and to say how confident the signals make you.

Facts about this customer and invoice:
- Invoices billed to them in total: {total_invoices}
- Of those, paid late: {invoices_paid_late}
- Of those, never paid at all: {invoices_defaulted}
- Promises to pay they have broken before: {broken_promises}
- Their typical invoice size: Rs {avg_invoice_inr}
- This invoice: Rs {amount_inr}
- Days overdue: {days_overdue}
- Prior dispute note on this invoice: {has_prior_dispute_note}
- Have they replied to us? {has_reply}

Write two sentences at most. Do not invent facts that are not listed above. Do not \
recommend an action. Do not mention the rules engine.

Return the category exactly as given to you: {rule_category}
"""
