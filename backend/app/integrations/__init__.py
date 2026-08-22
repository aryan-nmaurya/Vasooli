"""External systems: Razorpay, email providers, the Gemini transport. Phases 3, 6, 7.

Import rule (enforced): may NOT import `app.services`, `app.policy`, or `app.api`.
Dependencies point outward only, so every integration is mockable at this boundary —
which is what lets the eval harness run production code paths with the network off.

Each client is a thin, typed wrapper. Retry and failover live here; business rules
about when to call them do not.
"""
