"""Undo Tier-3 escalations that were recorded for reminders which never went out.

    uv run python -m scripts.repair_false_tier3_escalations           # report only
    uv run python -m scripts.repair_false_tier3_escalations --apply

Until 2026-09-01 the recovery cycle ignored the result of `deliver_reminder`. A send
refused before it reached the provider — most often "no verified sending domain" on a
live workspace — was still counted as sent, and at Tier 3 the invoice was escalated
with reason `tier_3_reached`. The dashboard states that reason as "all three automated
reminders have been sent", so an invoice whose customer had received nothing was parked
in human review under a claim that was false.

HUMAN_REVIEW also removes the invoice from the cadence, so the engine never returned to
it. The code fix stops new occurrences and revives stranded reminders, but it cannot
reach these rows: the cycle skips them precisely because they are escalated.

Only provably false escalations are touched: `escalation_reason = 'tier_3_reached'` AND
`reminders_sent = 0`. An invoice that genuinely received three reminders has
`reminders_sent = 3` and is left alone, as is any escalation for another reason —
a dispute or a manual handover is a human decision and not ours to undo.

Idempotent: rows repaired once no longer match.
"""

import argparse

from sqlmodel import Session, select

from app.core.constants import InvoiceStatus
from app.core.db import engine
from app.models import AuditAction, AuditActor, AuditLog, Invoice, Reminder

GREEN, YELLOW, DIM, RESET = "\033[32m", "\033[33m", "\033[2m", "\033[0m"


def find_false_escalations(session: Session) -> list[Invoice]:
    """Escalated as 'three reminders sent' while the counter says none were."""
    return list(
        session.exec(
            select(Invoice).where(
                Invoice.escalation_reason == "tier_3_reached",
                Invoice.reminders_sent == 0,
                Invoice.status == InvoiceStatus.HUMAN_REVIEW,
            )
        ).all()
    )


def repair(session: Session, invoice: Invoice) -> None:
    """Return the invoice to the cadence and record why.

    The reminder rows are deliberately left as they are. They are real history — an
    attempt was made and it failed — and the recovery cycle now revives a `dead` row
    for a tier that is still owed, so the customer receives the message that was
    already approved rather than a freshly drafted one.
    """
    invoice.status = InvoiceStatus.CHASING
    invoice.escalation_reason = None
    invoice.escalated_to_human_at = None
    session.add(invoice)
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.SYSTEM,
            action=AuditAction.RECOVERY_RESUMED,
            detail={
                "repair": "false_tier_3_escalation",
                "reason": (
                    "Escalated as tier_3_reached while reminders_sent was 0: the "
                    "Tier-3 send was refused before delivery, so no reminder reached "
                    "the customer. Returned to the recovery cadence."
                ),
            },
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the repair (default: report only)"
    )
    args = parser.parse_args()

    with Session(engine) as session:
        affected = find_false_escalations(session)
        if not affected:
            print(f"{GREEN}No false Tier-3 escalations found.{RESET}")
            return 0

        print(
            f"{YELLOW}{len(affected)} invoice(s) escalated as tier_3_reached "
            f"with 0 reminders sent:{RESET}"
        )
        for invoice in affected:
            stranded = session.exec(
                select(Reminder).where(
                    Reminder.invoice_id == invoice.id,
                    Reminder.sent_at.is_(None),  # type: ignore[union-attr]
                )
            ).all()
            detail = ", ".join(f"tier {r.tier} {r.delivery_state}" for r in stranded) or "none"
            print(f"  {invoice.invoice_number:<20} undelivered reminders: {detail}")
            print(
                f"{DIM}     -> status chasing, escalation cleared, "
                f"tier re-offered next cycle{RESET}"
            )

        if not args.apply:
            print(f"\n{DIM}Report only. Re-run with --apply to write.{RESET}")
            return 0

        for invoice in affected:
            repair(session, invoice)
        session.commit()
        print(f"\n{GREEN}Repaired {len(affected)} invoice(s).{RESET}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
