"""The boundary between the guided demo and real merchants' ledgers.

The operator dashboard is the demo's console. Keeping it there was supposed to be
row-level security's job — `app.api.deps.require_operator` binds the demo tenant on
every request — and that works exactly as intended when the application connects as a
role the policies apply to. It does nothing at all when the process connects as the
database owner or a superuser, which is what the deployment does today: with RLS
inert, `select(Invoice)` on an operator route returns live merchants' ledgers.

What that looked like in practice, with two live merchants registered: real customer
names and email addresses in the demo's invoice queue, real receivables folded into
the demo's headline numbers, and the write-off button working on a live merchant's
invoice — closing their payment link with it.

Filtering explicitly here does not depend on the connecting role, so the boundary holds
in either configuration rather than in whichever one happens to be deployed. It is also
the only option for the tables the dashboard reads that carry no `merchant_id` at all —
reminders, promises, payment links, dispute cases, inbound messages, reconciliation
events, external payments — which no policy could scope even with RLS in force.

This is deliberately one-directional. It scopes the *demo* console to demo merchants;
it says nothing about live merchants, who are authorized per-tenant in
`app.services.authorization` and never reach these routes.
"""

from sqlmodel import select

from app.models import Invoice, Merchant


def demo_invoice_ids():
    """A subquery selecting every invoice owned by a demo merchant.

    Returned as a subquery rather than a list of ids so it composes into an existing
    `WHERE` without a second round trip, and so a merchant created between the two
    queries cannot slip through.
    """
    return (
        select(Invoice.id)
        .join(Merchant, Merchant.id == Invoice.merchant_id)
        .where(Merchant.is_demo.is_(True))  # type: ignore[union-attr]
    )


def demo_invoices():
    """`select(Invoice)` restricted to the demo, for routes that list the ledger."""
    return select(Invoice).where(Invoice.id.in_(demo_invoice_ids()))  # type: ignore[attr-defined]


def is_demo_invoice(session, invoice: Invoice | None) -> bool:
    if invoice is None:
        return False
    merchant = session.get(Merchant, invoice.merchant_id)
    return merchant is not None and merchant.is_demo
