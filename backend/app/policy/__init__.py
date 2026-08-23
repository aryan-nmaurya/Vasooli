"""Deterministic decision layer. Doc §5.

Owns every rule about WHETHER and WHEN to contact a customer: cadence, cooldowns,
reminder caps, banned language, promise pauses, dispute routing.

Import rule (enforced by tests/architecture/test_layering.py): may import app.core
only. May NOT import app.core.db, app.integrations, app.services, app.ai, or any
clock. Time is passed in as `now`.

That purity is what lets Phase 11's eval harness run thousands of simulated days
against the real policy code rather than a reimplementation of it.

Nothing in app.ai can bypass this layer, because the AI layer produces text and this
layer decides what happens to it.
"""

from app.policy.decisions import PolicyCheck, PolicyDecision, RequiredAction
from app.policy.engine import evaluate_reminder, next_tier_for, tone_for_tier

__all__ = [
    "PolicyCheck",
    "PolicyDecision",
    "RequiredAction",
    "evaluate_reminder",
    "next_tier_for",
    "tone_for_tier",
]
