"""Whether a dispute signal pauses recovery. Customer Conversation Safety.

The AI reads a customer's message and says what it thinks the customer meant. This
module decides what happens as a result. That split is the point of the whole
feature: a model's opinion never reaches invoice state directly, it reaches THIS
function, and this function is deterministic, pure, and testable without a model.

**Confidence does not gate the pause, on purpose.** It would be natural to require, say,
0.8 before stopping the cadence — and it would be wrong. The two mistakes are not
symmetrical: pausing on a reply that turns out not to be a dispute costs a delay a
person can undo in one click, while chasing a customer who IS disputing costs the
relationship and cannot be undone at all. So any dispute signal pauses, and the
confidence is carried through to the merchant as information about how much to trust
the summary — not as a switch the machine flips.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.core.constants import TERMINAL_STATUSES, InvoiceStatus


class DisputeAction(StrEnum):
    #: Open a case and take the invoice out of the cadence.
    PAUSE_AND_OPEN_CASE = "pause_and_open_case"
    #: A case is already open for this invoice. Recording it again would be noise.
    ALREADY_PAUSED = "already_paused"
    #: The invoice is recovered or written off. Nothing left to pause.
    NO_RECOVERY_TO_PAUSE = "no_recovery_to_pause"
    #: The reply was not a dispute.
    NO_ACTION = "no_action"


@dataclass(frozen=True)
class DisputeDecision:
    action: DisputeAction
    reason: str

    @property
    def pauses_recovery(self) -> bool:
        return self.action is DisputeAction.PAUSE_AND_OPEN_CASE


def decide_dispute_action(
    *,
    is_dispute: bool,
    status: InvoiceStatus | str,
    case_already_open: bool,
) -> DisputeDecision:
    """Decide what a dispute signal means for an invoice.

    Pure: no database, no clock, no model. Everything it needs is an argument, which
    is what makes the table-driven tests in tests/unit/test_dispute_policy.py an
    exhaustive statement of the behaviour rather than a sample of it.
    """
    if not is_dispute:
        return DisputeDecision(
            action=DisputeAction.NO_ACTION,
            reason="Reply does not object to the invoice",
        )

    # `in`, not `is`. A status loaded from Postgres is a plain str while one assigned
    # in memory is the enum; StrEnum compares equal to both, identity does not. The
    # same trap has already caused a live bug on the dispute path in recovery.py.
    if status in TERMINAL_STATUSES:
        return DisputeDecision(
            action=DisputeAction.NO_RECOVERY_TO_PAUSE,
            reason=f"Invoice is {status} — there is no active recovery to pause",
        )

    if case_already_open:
        return DisputeDecision(
            action=DisputeAction.ALREADY_PAUSED,
            reason="A dispute case is already open for this invoice",
        )

    return DisputeDecision(
        action=DisputeAction.PAUSE_AND_OPEN_CASE,
        reason="Customer disputes this invoice — automated recovery paused for review",
    )


def decide_resume(*, case_is_open: bool, status: InvoiceStatus | str) -> DisputeDecision:
    """Decide whether recovery may restart after a human resolved a dispute.

    Resuming is a human's call, but it is still checked: an invoice that was paid or
    written off while the dispute was being worked must not be put back into the
    cadence by an operator clicking the button they had already decided to click.
    """
    if case_is_open:
        return DisputeDecision(
            action=DisputeAction.ALREADY_PAUSED,
            reason="The dispute is still open — resolve it before resuming recovery",
        )

    if status in TERMINAL_STATUSES:
        return DisputeDecision(
            action=DisputeAction.NO_RECOVERY_TO_PAUSE,
            reason=f"Invoice is {status} — recovery will not restart",
        )

    return DisputeDecision(
        action=DisputeAction.NO_ACTION,
        reason="Dispute resolved — invoice returns to the automated cadence",
    )
