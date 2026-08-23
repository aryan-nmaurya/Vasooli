"""Result types for the policy engine. Doc §5.

A decision carries the complete check list, not just a verdict. Two reasons: the
audit log has to show what was evaluated (Doc §3 Stage 6), and the dashboard renders
the same list verbatim — a visible "REJECTED — banned phrase 'legal action'" is the
most convincing artifact in the demo, and it only exists if rejections are recorded
with their reasoning intact.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RequiredAction(StrEnum):
    SEND = "send"
    #: Not now, but possibly later — cadence not yet due, cooldown active, promise in
    #: effect. The invoice stays in the automated queue.
    HOLD = "hold"
    #: Out of automation entirely. A human decides what happens next.
    ESCALATE_TO_HUMAN = "escalate_to_human"


@dataclass(frozen=True)
class PolicyCheck:
    name: str
    passed: bool
    detail: str
    #: What happens if this check fails. A failed HOLD is recoverable; a failed
    #: ESCALATE_TO_HUMAN takes the invoice out of automation for good.
    on_failure: RequiredAction = RequiredAction.HOLD

    def render(self) -> str:
        return f"{'✓' if self.passed else '✗'} {self.detail}"


@dataclass(frozen=True)
class PolicyDecision:
    approved: bool
    required_action: RequiredAction
    reason: str
    checks: list[PolicyCheck] = field(default_factory=list)
    invoice_number: str = ""
    proposed_tier: int = 0

    @property
    def failed_checks(self) -> list[PolicyCheck]:
        return [c for c in self.checks if not c.passed]

    def render(self) -> str:
        """The human-readable form from Doc §5.

        Goes into the audit log and onto the invoice timeline unchanged, so what a
        reviewer reads is the actual decision rather than a summary of it.
        """
        lines = [
            f"Invoice: {self.invoice_number}",
            f"Proposed action: Send Tier-{self.proposed_tier} reminder",
            *(c.render() for c in self.checks),
            f"Result: {'APPROVED' if self.approved else self.required_action.value.upper()}",
        ]
        if not self.approved:
            lines.append(f"Reason: {self.reason}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Structured form for the JSONB audit column."""
        return {
            "approved": self.approved,
            "required_action": self.required_action.value,
            "reason": self.reason,
            "invoice_number": self.invoice_number,
            "proposed_tier": self.proposed_tier,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks
            ],
            "rendered": self.render(),
        }
