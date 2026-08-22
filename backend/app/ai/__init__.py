"""AI reasoning layer. Phase 6.

Three advisory tasks: diagnose a reason category, draft reminder copy, extract a
promise from a customer reply. Each returns a validated pydantic object.

Import rule (enforced): may NOT import `app.integrations.email`,
`app.integrations.razorpay_client`, `app.services`, or `app.core.db`.

This is the architectural claim of the project made structural — the model cannot
send an email, cannot move money, and cannot write invoice status, because the code
it would need to do so is not reachable from here. It recommends; `app.policy`
decides; `app.services` acts.

Customer replies reaching this layer are UNTRUSTED input. They are data to extract
from, never instructions to follow.
"""
