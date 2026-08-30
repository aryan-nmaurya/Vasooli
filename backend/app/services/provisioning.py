"""Give every invoice a way to be paid. Doc §3 Stage 1.

The one rule that matters here is idempotency. Provisioning is retried after network
failures, re-run across batches, and triggered again by hand from the dashboard. If any
of those creates a second payment link, the customer ends up with two different places
to pay the same bill and reconciliation has to guess which one settled it.

Three things enforce that, in order of how much they are trusted:

1. A transaction-scoped advisory lock for this invoice, so two provisioning workers
   cannot check-then-create concurrently without locking payment/reply updates.
2. A pre-check for an existing link, which makes the common retry a cheap no-op.
3. A UNIQUE constraint on `payment_links.invoice_id`, which is what actually holds if
   the first two are ever bypassed.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.logging import get_logger
from app.integrations.razorpay_client import (
    RazorpayClient,
    RazorpayDuplicateReferenceError,
    RazorpayPermanentError,
    RazorpayTransientError,
    get_razorpay_client,
)
from app.models import (
    AuditAction,
    AuditActor,
    AuditLog,
    Customer,
    Invoice,
    Merchant,
    PaymentLink,
    PaymentLinkStatus,
)

log = get_logger("provisioning")


class ProvisioningError(Exception):
    """Provisioning failed for a reason worth reporting to a human."""


def reference_id_for(invoice: Invoice, *, is_demo: bool = True) -> str:
    """A stable, human-readable id echoed back by Razorpay.

    Live invoices use their globally unique UUID; demo invoices retain the frozen,
    human-readable invoice-number reference.
    """
    return f"vsl-{invoice.invoice_number}" if is_demo else f"vsl-{invoice.id.hex}"


def get_existing_link(session: Session, invoice_id: uuid.UUID) -> PaymentLink | None:
    return session.exec(select(PaymentLink).where(PaymentLink.invoice_id == invoice_id)).first()


def _create_or_adopt(client, invoice, customer, reference_id):
    """Create the link, or adopt the one Razorpay already has for this reference.

    A duplicate-reference rejection means the link exists upstream but our row does
    not — most often after a database reset. Creating a fresh one under a different
    reference would give the customer two places to pay the same invoice, so the
    existing link is adopted instead.
    """
    try:
        return client.create_payment_link(
            amount_paise=invoice.amount_paise,
            reference_id=reference_id,
            description=f"Invoice {invoice.invoice_number}",
            customer_name=customer.name,
            customer_email=customer.email,
            customer_phone=customer.phone,
            # invoice_id is the primary match path at reconciliation; invoice_number
            # is carried too so a human reading a webhook payload can tell what it is.
            notes={
                "invoice_id": str(invoice.id),
                "invoice_number": invoice.invoice_number,
            },
            accept_partial=True,
        )
    except RazorpayDuplicateReferenceError:
        existing = client.find_by_reference_id(reference_id)
        if existing is None:
            raise
        log.info(
            "provisioning.adopted_existing",
            invoice_number=invoice.invoice_number,
            link_id=existing.id,
        )
        return existing


def provision_for_invoice(
    session: Session,
    invoice_id: uuid.UUID,
    *,
    client: RazorpayClient | None = None,
) -> PaymentLink:
    """Create the payment link for one invoice, or return the one that exists.

    Safe to call repeatedly. Commits on success so a partially-completed batch keeps
    everything it managed to provision.
    """
    client = client or get_razorpay_client()

    # Serialize only provisioning for this invoice. Unlike SELECT FOR UPDATE, this
    # advisory lock does not block a payment webhook or a customer reply while the
    # Razorpay call is in flight. It releases automatically at commit/rollback.
    lock_key = invoice_id.int & ((1 << 63) - 1)
    session.exec(text("SELECT pg_advisory_xact_lock(:key)").bindparams(key=lock_key))

    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise ProvisioningError(f"invoice {invoice_id} does not exist")

    existing = get_existing_link(session, invoice.id)
    if existing is not None:
        log.info("provisioning.already_exists", invoice_number=invoice.invoice_number)
        return existing

    customer = session.get(Customer, invoice.customer_id)
    if customer is None:
        raise ProvisioningError(f"invoice {invoice.invoice_number} has no customer")

    merchant = session.get(Merchant, invoice.merchant_id)
    if merchant is None:
        raise ProvisioningError(f"invoice {invoice.invoice_number} has no merchant")
    reference_id = reference_id_for(invoice, is_demo=merchant.is_demo)

    try:
        result = _create_or_adopt(client, invoice, customer, reference_id)
    except (RazorpayPermanentError, RazorpayTransientError) as exc:
        session.add(
            AuditLog(
                invoice_id=invoice.id,
                actor=AuditActor.RAZORPAY,
                action=AuditAction.PAYMENT_LINK_FAILED,
                detail={
                    "invoice_number": invoice.invoice_number,
                    "error": str(exc),
                    "retryable": isinstance(exc, RazorpayTransientError),
                },
            )
        )
        session.commit()
        raise ProvisioningError(str(exc)) from exc

    link = PaymentLink(
        invoice_id=invoice.id,
        razorpay_payment_link_id=result.id,
        reference_id=result.reference_id or reference_id,
        short_url=result.short_url,
        status=result.status or PaymentLinkStatus.CREATED,
        amount_expected_paise=invoice.amount_paise,
        amount_paid_paise=result.amount_paid_paise,
        accept_partial=True,
        raw_response=result.raw,
    )
    session.add(link)
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.RAZORPAY,
            action=AuditAction.PAYMENT_LINK_CREATED,
            detail={
                "invoice_number": invoice.invoice_number,
                "payment_link_id": result.id,
                "reference_id": link.reference_id,
                "short_url": result.short_url,
                "amount_paise": invoice.amount_paise,
            },
        )
    )

    try:
        session.commit()
    except IntegrityError:
        # Another worker won the race between our check and our insert. The unique
        # constraint did its job; adopt their link rather than failing the caller.
        session.rollback()
        winner = get_existing_link(session, invoice.id)
        if winner is None:
            raise
        log.warning("provisioning.lost_race", invoice_number=invoice.invoice_number)
        return winner

    session.refresh(link)
    return link


def provision_batch(
    session: Session,
    *,
    invoice_ids: list[uuid.UUID] | None = None,
    limit: int | None = None,
    client: RazorpayClient | None = None,
) -> dict[str, int | list[dict[str, str]]]:
    """Provision every invoice that still needs a payment link.

    One invoice failing must not stop the rest: a batch that aborts halfway leaves the
    ledger in a state nobody can reason about, and Razorpay's test mode rate-limits
    hard enough that transient failures are expected rather than exceptional.
    """
    client = client or get_razorpay_client()

    query = select(Invoice).where(
        ~select(PaymentLink.invoice_id).where(PaymentLink.invoice_id == Invoice.id).exists()
    )
    if invoice_ids:
        query = query.where(Invoice.id.in_(invoice_ids))  # type: ignore[attr-defined]
    if limit:
        query = query.limit(limit)

    pending = session.exec(query).all()
    provisioned = 0
    failed: list[dict[str, str]] = []

    for invoice in pending:
        try:
            provision_for_invoice(session, invoice.id, client=client)
            provisioned += 1
        except ProvisioningError as exc:
            failed.append({"invoice_number": invoice.invoice_number, "error": str(exc)})
            log.warning(
                "provisioning.failed",
                invoice_number=invoice.invoice_number,
                error=str(exc),
            )

    log.info("provisioning.batch_complete", provisioned=provisioned, failed=len(failed))
    return {"considered": len(pending), "provisioned": provisioned, "failed": failed}
