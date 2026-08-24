"""Walk the customer-conversation-safety slice end to end.

    uv run python -m scripts.demo_dispute --invoice INV-3004
    uv run python -m scripts.demo_dispute --invoice INV-3004 --resolve --resume

Runs the same code the dashboard runs. Nothing here is a shortcut around the system:
the reply goes through `handle_reply`, the pause is made by `app.policy.disputes`, and
the resolution goes through `resolve_dispute`. What it saves you is clicking.

DEMO SIMULATION: the customer's reply is injected rather than received by email.
Inbound mail parsing needs a verified domain and a provider feature and is not
implemented. Everything after the message arrives is the production path.
"""

import argparse
import sys

from sqlmodel import Session, select

from app.core.constants import InvoiceStatus
from app.core.db import engine
from app.models import AuditLog, Invoice
from app.services.disputes import open_case_for, resolve_dispute
from app.services.replies import handle_reply

DEFAULT_DISPUTE = (
    "We were billed for 12 units but only received 9. The delivery note is signed for "
    "9. Please check before we pay anything."
)


def show(session: Session, invoice: Invoice, *, limit: int = 12) -> None:
    entries = session.exec(
        select(AuditLog).where(AuditLog.invoice_id == invoice.id).order_by(AuditLog.created_at)
    ).all()
    print("\n  audit trail (most recent last):")
    for entry in list(entries)[-limit:]:
        print(f"    {entry.created_at:%d %b %H:%M}  {entry.actor:<22} {entry.action}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invoice", required=True, help="invoice number, e.g. INV-3004")
    parser.add_argument("--body", default=DEFAULT_DISPUTE, help="the customer's message")
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="call Gemini for the analysis. Off by default so the demo is deterministic.",
    )
    parser.add_argument("--resolve", action="store_true", help="close the open case")
    parser.add_argument(
        "--resume", action="store_true", help="with --resolve, put the invoice back in the cadence"
    )
    parser.add_argument("--note", default="Delivery note checked.", help="resolution note")
    args = parser.parse_args()

    with Session(engine) as session:
        invoice = session.exec(
            select(Invoice).where(Invoice.invoice_number == args.invoice)
        ).first()
        if invoice is None:
            print(f"No invoice {args.invoice}.", file=sys.stderr)
            return 1

        if args.resolve:
            case = open_case_for(session, invoice.id)
            if case is None:
                print(f"{args.invoice} has no open dispute.", file=sys.stderr)
                return 1

            case, resumed = resolve_dispute(
                session,
                case,
                resolved_by="human:demo@vasooli.local",
                note=args.note,
                resume_recovery=args.resume,
            )
            session.commit()
            session.refresh(invoice)

            print(f"\n  Dispute resolved on {args.invoice}")
            print(f"    note:            {case.resolution_note}")
            print(f"    recovery resumed: {resumed}")
            print(f"    invoice status:   {invoice.status}")
            show(session, invoice)
            return 0

        print(f"\n  DEMO SIMULATION — injecting a customer reply on {args.invoice}")
        print(f'    "{args.body}"')

        before = invoice.status
        outcome = handle_reply(session, invoice, args.body, use_llm=args.use_llm)
        session.refresh(invoice)
        case = open_case_for(session, invoice.id)

        print("\n  What happened:")
        print(f"    dispute detected: {outcome.is_complaint}")
        print(f"    invoice status:   {before} → {invoice.status}")
        if case is not None:
            print(f"    case opened:      {case.id}")
            print(f"    reason:           {case.reason}")
            print(f"    summary:          {case.summary}")
            print(f"    confidence:       {round(case.confidence * 100)}%")
            print(f"    detected by:      {case.detected_by}")
            for fact in case.facts:
                print(f"      · {fact}")

        paused = invoice.status == InvoiceStatus.HUMAN_REVIEW
        print(f"\n  Recovery paused: {paused}")
        show(session, invoice)
        print("\n  Next: open the invoice in the dashboard, or run with --resolve --resume.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
