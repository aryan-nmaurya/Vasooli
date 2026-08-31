"""Payments an operator records by hand, and the balance arithmetic that follows.

Vasooli reconciles Razorpay Payment Links automatically and correctly. Everything else
a real Indian B2B customer might do — NEFT to the merchant's current account, a UPI
transfer straight to the VPA, a cheque, a settlement negotiated over the phone — was
invisible, so the invoice stayed open and the customer kept getting chased. This module
is the way that money gets in.

Three rules hold everything together:

1. **The two sources of truth never share a column.** Razorpay's running total lives in
   `invoice.link_paid_paise` and is applied with `max()`; operator entries live in
   `invoice.external_paid_paise` and are additive. `amount_paid_paise` is the sum,
   recomputed here and in reconciliation, never edited directly.
2. **Nothing is edited or deleted.** A mistaken entry is reversed, which leaves the
   claim and the retraction both visible and lets the balance be recomputed from the
   rows that still stand.
3. **The invoice row is locked for the whole recomputation.** A webhook landing halfway
   through would otherwise read a balance nobody ever wrote.
"""

import uuid
from datetime import date

from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.constants import InvoiceStatus, PromiseStatus
from app.core.logging import get_logger
from app.core.money import format_inr
from app.models import (
    AuditAction,
    AuditActor,
    AuditLog,
    ExternalPayment,
    Invoice,
    PaymentLink,
    Promise,
)
from app.models.external_payment import PaymentMethod
from app.services.closure import close_link_for_invoice

log = get_logger("manual_payments")

#: How each method is labelled in the dashboard. Defined here rather than in the UI so
#: the form can only ever offer a method this module would accept.
METHOD_LABELS = {
    PaymentMethod.BANK_TRANSFER: "Bank transfer (NEFT / RTGS / IMPS)",
    PaymentMethod.UPI: "UPI",
    PaymentMethod.CHEQUE: "Cheque",
    PaymentMethod.CASH: "Cash",
    PaymentMethod.RAZORPAY_UNLINKED: "Razorpay payment outside this link",
    PaymentMethod.ADJUSTMENT: "Credit note or agreed adjustment",
}


def payment_view(payment: ExternalPayment) -> dict:
    """One recorded payment, shaped for the dashboard.

    `recorded_by` and the reversal fields are always included. A hand-entered payment
    that does not say who entered it is an assertion with no author, which is exactly
    what this table exists not to be.
    """
    return {
        "id": str(payment.id),
        "amount_display": format_inr(payment.amount_paise),
        "amount_paise": payment.amount_paise,
        "method": payment.method,
        "method_label": METHOD_LABELS.get(payment.method, payment.method),
        "reference": payment.reference,
        "received_on": str(payment.received_on),
        "note": payment.note,
        "recorded_by": payment.recorded_by,
        "recorded_at": payment.recorded_at.isoformat(),
        "reversed_at": payment.reversed_at.isoformat() if payment.reversed_at else None,
        "reversed_by": payment.reversed_by,
        "reversal_reason": payment.reversal_reason,
        "active": payment.is_active,
    }


class ManualPaymentError(ValueError):
    """The entry cannot be accepted. Carries a message meant for the operator."""


def _active_external_total(session: Session, invoice_id: uuid.UUID) -> int:
    """Recomputed from the rows, not incremented.

    An increment drifts the moment one reversal is missed, and a balance that drifts is
    a balance that chases paid customers. Summing is cheap — an invoice has a handful of
    manual entries, not thousands — and it cannot drift.
    """
    rows = session.exec(
        select(ExternalPayment).where(ExternalPayment.invoice_id == invoice_id)
    ).all()
    return sum(row.amount_paise for row in rows if row.is_active)


def _apply_balance(session: Session, invoice: Invoice) -> tuple[str, str]:
    """Recompute the total and the status the balance implies.

    Returns (previous_status, new_status). Deliberately does not commit — the caller
    owns the transaction, because the ExternalPayment row and the balance it changes
    have to land together or not at all.
    """
    previous_status = str(invoice.status)
    invoice.external_paid_paise = _active_external_total(session, invoice.id)
    invoice.amount_paid_paise = invoice.provider_net_paid_paise + invoice.external_paid_paise

    if invoice.is_fully_paid:
        invoice.status = InvoiceStatus.RECOVERED
        invoice.recovered_at = invoice.recovered_at or utcnow()
        _resolve_active_promise(session, invoice, PromiseStatus.KEPT)
    elif invoice.status == InvoiceStatus.RECOVERED:
        # The balance no longer supports "recovered" — a reversal took it back below
        # the invoice total. Returning it to the queue is the honest outcome, and
        # `recovered_at` is cleared so the recovery metrics do not keep counting an
        # invoice that is owed again.
        invoice.recovered_at = None
        invoice.status = (
            InvoiceStatus.PARTIALLY_PAID if invoice.amount_paid_paise > 0 else InvoiceStatus.CHASING
        )
    elif invoice.amount_paid_paise > 0 and invoice.status in {
        InvoiceStatus.PENDING,
        InvoiceStatus.CHASING,
    }:
        invoice.status = InvoiceStatus.PARTIALLY_PAID

    session.add(invoice)
    return previous_status, str(invoice.status)


def sync_erp_adjustment(
    session: Session,
    *,
    invoice: Invoice,
    provider: str,
    source_id: str,
    amount_paise: int,
    received_on: date,
    is_credit: bool = False,
) -> bool:
    """Synchronize one provider-owned payment or credit without losing its history.

    ERP entries are provider assertions, not human assertions and not Razorpay-signed
    collection events. They therefore use the external-payment column but carry a
    stable ``erp:`` reference and a system actor. When a source record changes, the
    previous row is reversed and a versioned replacement is appended; nothing is
    edited away and the balance is recomputed from standing rows.
    """
    amount_paise = max(0, amount_paise)
    kind = "credit" if is_credit else "payment"
    reference_prefix = f"erp:{provider}:{kind}:{source_id}:"
    active = [
        row
        for row in session.exec(
            select(ExternalPayment).where(ExternalPayment.invoice_id == invoice.id)
        ).all()
        if row.reference.startswith(reference_prefix) and row.is_active
    ]
    if len(active) == 1 and active[0].amount_paise == amount_paise:
        return False

    now = utcnow()
    for row in active:
        row.reversed_at = now
        row.reversed_by = f"system:erp:{provider}"
        row.reversal_reason = "Superseded by a newer ERP source version"
        session.add(row)

    if amount_paise:
        reference = f"{reference_prefix}{amount_paise}:{uuid.uuid4().hex[:8]}"
        session.add(
            ExternalPayment(
                invoice_id=invoice.id,
                amount_paise=amount_paise,
                method=(PaymentMethod.ADJUSTMENT if is_credit else PaymentMethod.BANK_TRANSFER),
                reference=reference,
                received_on=received_on,
                note=f"Synchronized from {provider} {kind} {source_id}",
                recorded_by=f"system:erp:{provider}",
            )
        )
    session.flush()
    previous_status, new_status = _apply_balance(session, invoice)
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.SYSTEM,
            action=(
                AuditAction.ERP_CREDIT_APPLIED if is_credit else AuditAction.ERP_PAYMENT_APPLIED
            ),
            detail={
                "provider": provider,
                "source_id": source_id,
                "amount_paise": amount_paise,
                "previous_status": previous_status,
                "new_status": new_status,
                "verification": "erp_asserted",
            },
        )
    )
    return True


def _resolve_active_promise(session: Session, invoice: Invoice, status: str) -> None:
    promise = session.exec(
        select(Promise).where(
            Promise.invoice_id == invoice.id,
            Promise.status == PromiseStatus.ACTIVE,
        )
    ).first()
    if promise is None:
        return
    promise.status = status
    promise.resolved_at = utcnow()
    session.add(promise)
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.SYSTEM,
            action=AuditAction.PROMISE_KEPT,
            detail={"promise_id": str(promise.id), "source": "external_payment"},
        )
    )


def record_external_payment(
    session: Session,
    *,
    invoice_id: uuid.UUID,
    amount_paise: int,
    method: str,
    reference: str,
    received_on: date,
    note: str,
    actor: str,
) -> ExternalPayment:
    """Record money that arrived outside a Vasooli payment link.

    Commits. On return the invoice balance, status, any active promise, and the payment
    link are all consistent with the new total.
    """
    if method not in PaymentMethod.ALL:
        raise ManualPaymentError(f"Unknown payment method: {method}")
    if amount_paise <= 0:
        raise ManualPaymentError("Amount must be greater than zero")

    reference = reference.strip()
    if not reference:
        raise ManualPaymentError("A reference is required — a UTR, cheque number, or payment id")

    # Lock first, so two operators entering the same bank statement at once cannot both
    # read the old balance and both decide the invoice is now settled.
    invoice = session.exec(
        select(Invoice).where(Invoice.id == invoice_id).with_for_update()
    ).first()
    if invoice is None:
        raise ManualPaymentError("Invoice not found")

    if session.exec(
        select(ExternalPayment).where(
            ExternalPayment.invoice_id == invoice_id,
            ExternalPayment.reference == reference,
            ExternalPayment.reversed_at.is_(None),  # type: ignore[union-attr]
        )
    ).first():
        raise ManualPaymentError(
            f"A payment with reference {reference!r} is already recorded against "
            f"{invoice.invoice_number}"
        )

    # Overpayment is allowed and is not an error — a customer settling two invoices
    # with one transfer, or paying a rounded figure, is ordinary. It is worth flagging
    # in the trail, because it is also what a typo looks like.
    overpays_by = max(
        0,
        invoice.link_paid_paise
        + _active_external_total(session, invoice_id)
        + amount_paise
        - invoice.amount_paise,
    )

    payment = ExternalPayment(
        invoice_id=invoice_id,
        amount_paise=amount_paise,
        method=method,
        reference=reference,
        received_on=received_on,
        note=note[:1000],
        recorded_by=actor,
    )
    session.add(payment)
    session.flush()  # so the sum below sees this row

    previous_status, new_status = _apply_balance(session, invoice)

    session.add(
        AuditLog(
            invoice_id=invoice_id,
            actor=actor,
            action=AuditAction.EXTERNAL_PAYMENT_RECORDED,
            detail={
                "external_payment_id": str(payment.id),
                "amount_paise": amount_paise,
                "method": method,
                "reference": reference,
                "received_on": str(received_on),
                "note": note[:300],
                "previous_status": previous_status,
                "new_status": new_status,
                "total_paid_paise": invoice.amount_paid_paise,
                "link_paid_paise": invoice.link_paid_paise,
                "external_paid_paise": invoice.external_paid_paise,
                "overpaid_by_paise": overpays_by,
                # Said plainly in the record itself, because this is the one money
                # entry in the system that nobody verified against a provider.
                "verification": "operator_asserted",
            },
        )
    )
    session.commit()
    session.refresh(payment)
    session.refresh(invoice)

    log.info(
        "manual_payments.recorded",
        invoice_number=invoice.invoice_number,
        method=method,
        amount_paise=amount_paise,
        new_status=new_status,
    )

    # Outside the transaction, exactly as reconciliation does it: an external HTTP call
    # held open across a database transaction turns a slow Razorpay into a lock on the
    # invoice row, and a timeout into a rolled-back payment.
    if invoice.link_should_be_closed:
        _close_link(session, invoice)

    return payment


def reverse_external_payment(
    session: Session,
    *,
    payment_id: uuid.UUID,
    reason: str,
    actor: str,
) -> ExternalPayment:
    """Retract an entry that should not have been made, and recompute the balance.

    The correction path for a mistyped amount, a payment credited to the wrong invoice,
    or a cheque that bounced. It is not a refund lifecycle: Vasooli does not ingest
    Razorpay refunds or chargebacks, and this does not pretend to. What it does mean is
    that a recovered invoice can go back to being owed, which is the state the system
    previously could not represent at all.
    """
    reason = reason.strip()
    if not reason:
        raise ManualPaymentError("A reason is required to reverse a recorded payment")

    payment = session.get(ExternalPayment, payment_id)
    if payment is None:
        raise ManualPaymentError("Recorded payment not found")
    if not payment.is_active:
        raise ManualPaymentError("This payment has already been reversed")

    invoice = session.exec(
        select(Invoice).where(Invoice.id == payment.invoice_id).with_for_update()
    ).one()

    payment.reversed_at = utcnow()
    payment.reversed_by = actor
    payment.reversal_reason = reason[:500]
    session.add(payment)
    session.flush()

    previous_status, new_status = _apply_balance(session, invoice)

    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=actor,
            action=AuditAction.EXTERNAL_PAYMENT_REVERSED,
            detail={
                "external_payment_id": str(payment.id),
                "amount_paise": payment.amount_paise,
                "reference": payment.reference,
                "reason": reason[:300],
                "previous_status": previous_status,
                "new_status": new_status,
                "total_paid_paise": invoice.amount_paid_paise,
                # The link was cancelled when the invoice settled and is not reopened
                # here. Razorpay links cannot be un-cancelled, so an invoice that comes
                # back owed needs a fresh one — surfaced to the operator rather than
                # quietly left without a way to pay.
                "payment_link_needs_reprovisioning": new_status != InvoiceStatus.RECOVERED,
            },
        )
    )
    session.commit()
    session.refresh(payment)

    log.warning(
        "manual_payments.reversed",
        invoice_number=invoice.invoice_number,
        amount_paise=payment.amount_paise,
        new_status=new_status,
    )
    return payment


def _close_link(session: Session, invoice: Invoice) -> None:
    """Best effort. A closure failure is recorded on the link and retried by the sweep."""
    try:
        close_link_for_invoice(session, invoice.id)
    except Exception:  # noqa: BLE001
        log.exception("manual_payments.closure_failed", invoice_number=invoice.invoice_number)


def payments_for(session: Session, invoice_id: uuid.UUID) -> list[ExternalPayment]:
    """Every manual entry against one invoice, newest first — reversed ones included.

    Reversed rows are returned deliberately. A balance that once said "paid" and now
    says "owed" is exactly the history an operator needs to see when a customer asks
    why they are being chased again.
    """
    return list(
        session.exec(
            select(ExternalPayment)
            .where(ExternalPayment.invoice_id == invoice_id)
            .order_by(ExternalPayment.recorded_at.desc())  # type: ignore[attr-defined]
        ).all()
    )


def link_for(session: Session, invoice_id: uuid.UUID) -> PaymentLink | None:
    return session.exec(select(PaymentLink).where(PaymentLink.invoice_id == invoice_id)).first()
