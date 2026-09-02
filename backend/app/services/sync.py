"""Reconcile directly from Razorpay, as a safety net for webhooks that never arrive.

Webhooks are the primary path and are idempotent, so this matters only when a delivery
is lost entirely — which is not hypothetical. A payment made while the receiver is
unreachable (no tunnel running, a deploy in progress, a network blip) leaves Razorpay
holding money we have no record of, and no amount of retrying on their side helps once
they have given up.

**Razorpay remains the source of truth.** This does not invent an event: it asks
Razorpay what it thinks the link's state is, and reconciles from that answer through
exactly the same `process_event` path a webhook would take. The event id is derived
from the link and the amount, so re-running the sync is a no-op rather than a second
payment.
"""

import uuid

from sqlmodel import Session, select

from app.core.logging import get_logger
from app.integrations.razorpay_client import (
    RazorpayClient,
    RazorpayPermanentError,
    RazorpayTransientError,
)
from app.models import (
    AuditAction,
    AuditActor,
    AuditLog,
    Invoice,
    PaymentLink,
    ReconciliationEvent,
)
from app.models.reconciliation_event import EventStatus
from app.services.payment_connections import (
    PaymentConnectionRequiredError,
    razorpay_client_for_merchant,
)
from app.services.reconciliation import process_event

log = get_logger("sync")


def _event_id_for(link_id: str, amount_paid_paise: int) -> str:
    """Deterministic id, so re-running the sync cannot double-count.

    Keyed on the amount as well as the link: a later, larger payment on the same link
    is genuinely a new event and should reconcile, while re-checking an unchanged link
    collides with the row already stored.
    """
    return f"sync:{link_id}:{amount_paid_paise}"


def sync_payment_links(
    session: Session,
    *,
    client: RazorpayClient | None = None,
    limit: int = 100,
    invoice_number: str | None = None,
) -> dict[str, int]:
    """Ask Razorpay about links we believe are still unpaid, and reconcile any that are.

    Only checks links that could still change: a link we already recorded as fully paid
    and closed has nothing to tell us, and asking would burn rate limit for nothing.
    """
    # `client` stays an override for tests and for the single-invoice demo path. When
    # it is not given, each link is fetched with the credentials of the account it was
    # created on — resolved per merchant below, not once for the whole batch.
    override = client

    query = select(PaymentLink)
    if invoice_number:
        invoice = session.exec(
            select(Invoice).where(Invoice.invoice_number == invoice_number)
        ).first()
        if invoice is None:
            return {"checked": 0, "recovered": 0, "errors": 0}
        query = query.where(PaymentLink.invoice_id == invoice.id)

    candidates = []
    for link in session.exec(query.limit(limit)).all():
        invoice = session.get(Invoice, link.invoice_id)
        if invoice is None or invoice.is_fully_paid:
            continue
        candidates.append((link, invoice))

    checked = recovered = errors = 0

    # One resolved client per merchant, not per link: a batch commonly holds many
    # links for the same merchant and re-resolving would decrypt the same credentials
    # over and over.
    clients: dict[uuid.UUID, object] = {}

    def client_for(merchant_id: uuid.UUID):
        if override is not None:
            return override
        if merchant_id not in clients:
            clients[merchant_id] = razorpay_client_for_merchant(session, merchant_id)
        return clients[merchant_id]

    for link, invoice in candidates:
        checked += 1
        try:
            client = client_for(invoice.merchant_id)
        except PaymentConnectionRequiredError as exc:
            # Not an error to retry: the merchant disconnected, or never connected.
            # Counted and logged so it surfaces, then skipped — hammering a link we
            # have no credentials for would burn rate limit for every merchant.
            errors += 1
            log.warning(
                "sync.no_connection",
                invoice_number=invoice.invoice_number,
                merchant_id=str(invoice.merchant_id),
                error=str(exc)[:200],
            )
            continue
        try:
            remote = client.fetch_payment_link(link.razorpay_payment_link_id)
        except (RazorpayPermanentError, RazorpayTransientError) as exc:
            errors += 1
            log.warning(
                "sync.fetch_failed",
                invoice_number=invoice.invoice_number,
                error=str(exc)[:200],
            )
            continue

        # Compared against the LINK total, not the invoice's combined balance. An
        # invoice partly settled by a hand-recorded bank transfer has a larger combined
        # balance than the link will ever report, and comparing against that would make
        # the sync skip a genuine link payment forever.
        if remote.amount_paid_paise <= invoice.link_paid_paise:
            continue  # nothing new

        event_id = _event_id_for(link.razorpay_payment_link_id, remote.amount_paid_paise)
        if session.exec(
            select(ReconciliationEvent).where(ReconciliationEvent.provider_event_id == event_id)
        ).first():
            continue  # already reconciled from a previous sync

        # Build the event from Razorpay's own response, in the shape a webhook would
        # have delivered, and put it through the identical processing path. Nothing
        # here is invented; every figure came from the API call above.
        event = ReconciliationEvent(
            provider_event_id=event_id,
            event_type=(
                "payment_link.paid"
                if remote.amount_paid_paise >= invoice.amount_paise
                else "payment_link.partially_paid"
            ),
            raw_payload={
                "entity": "event",
                "event": "payment_link.paid",
                "source": "razorpay_sync",
                "payload": {"payment_link": {"entity": remote.raw}},
            },
            # Not signature-verified, because this did not arrive as a signed webhook.
            # It came from an authenticated call we made to Razorpay, which is a
            # stronger guarantee — but recording it as "verified" would blur the two.
            signature_verified=False,
            status=EventStatus.RECEIVED,
        )
        session.add(event)
        session.add(
            AuditLog(
                invoice_id=invoice.id,
                actor=AuditActor.RAZORPAY,
                action=AuditAction.RECONCILIATION_SYNCED,
                detail={
                    "reason": "payment found by direct sync — no webhook was received",
                    "payment_link_id": link.razorpay_payment_link_id,
                    "amount_paid_paise": remote.amount_paid_paise,
                    "remote_status": remote.status,
                },
            )
        )
        session.commit()
        session.refresh(event)

        process_event(session, event)
        session.refresh(invoice)
        if invoice.is_fully_paid:
            recovered += 1

        log.info(
            "sync.reconciled",
            invoice_number=invoice.invoice_number,
            amount_paid_paise=remote.amount_paid_paise,
        )

    if checked:
        log.info("sync.complete", checked=checked, recovered=recovered, errors=errors)
    return {"checked": checked, "recovered": recovered, "errors": errors}
