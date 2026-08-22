"""Deterministic decision layer. Phase 5.

Owns every rule about WHETHER and WHEN to contact a customer: cadence, cooldowns,
reminder caps, banned language, promise pauses, dispute routing.

Import rule (enforced): may import `app.core` and `app.models` for types only.
May NOT import `app.core.db`, `app.integrations`, `app.services`, `app.ai`, httpx,
or any clock. Time is passed in as `now`.

That makes the whole layer pure — no DB, no network, no wall clock — which is what
lets Phase 11's eval harness run thousands of simulated days against the real policy
code rather than a reimplementation of it.

Nothing in `app.ai` can bypass this layer, because the AI layer produces text and
this layer decides what happens to it.
"""
