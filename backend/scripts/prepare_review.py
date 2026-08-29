"""Put the ledger into a known state before someone reviews it.

    uv run python -m scripts.prepare_review          # stage on top of current data
    uv run python -m scripts.prepare_review --check  # report state, change nothing

A reviewer arriving unannounced sees whatever the last test run left behind. That is
usually eight quiet invoices with nothing to discover, which wastes the visit: the
behaviour worth showing — recovery pausing because a customer objected — only exists
after somebody has already exercised the loop.

This stages the three states that make the queue worth reading, through the same
service functions the live paths call. It is seeding, not faking: `handle_reply` here
is the identical function the inbound-email webhook invokes, and every audit row,
policy decision and AI call happens exactly as it would for a real message.

What it does NOT do is claim a real customer sent anything. The demo ledger's
customers are invented, and the reviewer guide says so.
"""

import argparse

from sqlmodel import Session, select

from app.core.constants import InvoiceStatus, PromiseStatus
from app.core.db import engine
from app.models import DisputeCase, Invoice, Promise
from app.services.disputes import open_case_for
from app.services.replies import handle_reply

#: Which invoice plays which part. Chosen from the demo set's own crib sheet:
#: 3003 is mid-cadence and cash-constrained, so a dispute on it is plausible; 3002 is
#: a clean payer, so a promise reads naturally.
DISPUTE_INVOICE = "INV-3003"
PROMISE_INVOICE = "INV-3002"

DISPUTE_BODY = (
    "We were billed for 12 units but only received 9. The delivery note is signed "
    "for 9. Please check before we pay anything."
)
PROMISE_BODY = "Cash is tight this month — I'll clear this by the 28th."


def report(session: Session) -> int:
    """Print what a reviewer would currently find."""
    invoices = session.exec(select(Invoice).order_by(Invoice.invoice_number)).all()
    open_cases = session.exec(select(DisputeCase).where(DisputeCase.status == "open")).all()
    promises = session.exec(select(Promise).where(Promise.status == PromiseStatus.ACTIVE)).all()
    recovered = [i for i in invoices if i.status == InvoiceStatus.RECOVERED]

    print(f"\n  {len(invoices)} invoices")
    for inv in invoices:
        marks = []
        if any(c.invoice_id == inv.id for c in open_cases):
            marks.append("DISPUTE OPEN")
        if any(p.invoice_id == inv.id for p in promises):
            marks.append("promise active")
        print(f"    {inv.invoice_number:<12} {str(inv.status):<16} {' · '.join(marks)}")

    print(f"\n  open disputes:   {len(open_cases)}")
    print(f"  active promises: {len(promises)}")
    print(f"  recovered:       {len(recovered)}")

    ready = bool(open_cases) and bool(promises)
    print(
        "\n  Ready for review."
        if ready
        else "\n  NOT staged — run without --check to stage the missing states."
    )
    return 0 if ready else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="report state without changing anything"
    )
    args = parser.parse_args()

    with Session(engine) as session:
        if args.check:
            return report(session)

        invoices = {i.invoice_number: i for i in session.exec(select(Invoice)).all()}

        # --- A dispute, so recovery is visibly paused for a stated reason -------
        target = invoices.get(DISPUTE_INVOICE)
        if target is None:
            print(f"  {DISPUTE_INVOICE} not in the ledger — run scripts.demo_reset first.")
            return 1
        if open_case_for(session, target.id) is not None:
            print(f"  {DISPUTE_INVOICE}: dispute already open, left alone")
        else:
            # use_llm=True on purpose: the reviewer should see a real model reading,
            # with extracted claims, not the regex fallback's placeholder summary.
            handle_reply(session, target, DISPUTE_BODY, use_llm=True)
            case = open_case_for(session, target.id)
            print(f"  {DISPUTE_INVOICE}: dispute opened — {case.reason if case else 'n/a'}")

        # --- A promise, so the other pause reason is on screen too --------------
        target = invoices.get(PROMISE_INVOICE)
        if target is None:
            print(f"  {PROMISE_INVOICE} not in the ledger — skipped")
        else:
            existing = session.exec(
                select(Promise).where(
                    Promise.invoice_id == target.id,
                    Promise.status == PromiseStatus.ACTIVE,
                )
            ).first()
            if existing is not None:
                print(f"  {PROMISE_INVOICE}: promise already active, left alone")
            else:
                handle_reply(session, target, PROMISE_BODY, use_llm=True)
                print(f"  {PROMISE_INVOICE}: promise logged, escalation paused")

        session.commit()
        print()
        return report(session)


if __name__ == "__main__":
    raise SystemExit(main())
