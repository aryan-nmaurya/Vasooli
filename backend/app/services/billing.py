"""Razorpay subscription state, plans and enforceable entitlements."""

import hashlib
import hmac
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.config import settings
from app.core.constants import TERMINAL_STATUSES
from app.integrations.razorpay_client import get_razorpay_client
from app.integrations.razorpay_signature import verify_signature
from app.models import (
    BillingEntitlement,
    BillingEvent,
    BillingPlan,
    BillingSubscription,
    Invoice,
    Merchant,
    MerchantInvitation,
    MerchantMembership,
)

PLAN_CATALOG = {
    "starter": (199_900, 100, 5),
    "growth": (599_900, 500, 15),
    "scale": (1_499_900, 2_000, 50),
}

ACTIVE_STATES = {"active", "authenticated"}
PROVIDER_STATUS_MAP = {
    "created": "created",
    "authenticated": "authenticated",
    "active": "active",
    "pending": "past_due",
    "past_due": "past_due",
    "halted": "paused",
    "paused": "paused",
    "cancelled": "cancelled",
    "completed": "cancelled",
    "expired": "expired",
}


class BillingEntitlementError(ValueError):
    """The merchant cannot perform a billable live operation."""


def verify_billing_signature(raw_body: bytes, signature: str | None) -> bool:
    return verify_signature(raw_body, signature)


def ensure_plans(session: Session) -> list[BillingPlan]:
    plans = []
    for slug, (amount, invoices, seats) in PLAN_CATALOG.items():
        provider_plan_id = getattr(settings, f"razorpay_plan_id_{slug}", None)
        plan = session.exec(
            select(BillingPlan).where(BillingPlan.slug == slug, BillingPlan.version == 1)
        ).first()
        if plan is None:
            plan = BillingPlan(
                slug=slug,
                name=slug.title(),
                amount_paise=amount,
                included_active_invoices=invoices,
                included_seats=seats,
                razorpay_plan_id=provider_plan_id,
            )
            session.add(plan)
        elif provider_plan_id and plan.razorpay_plan_id is None:
            plan.razorpay_plan_id = provider_plan_id
            session.add(plan)
        elif provider_plan_id and plan.razorpay_plan_id != provider_plan_id:
            # Provider plan IDs are immutable: changing one in configuration should
            # be accompanied by a new database version, never an in-place rewrite.
            raise ValueError(f"Razorpay plan mapping changed for immutable plan {slug}")
        plans.append(plan)
    session.flush()
    return plans


def subscription_is_active(session: Session, merchant_id: uuid.UUID) -> bool:
    subscription = session.exec(
        select(BillingSubscription)
        .where(BillingSubscription.merchant_id == merchant_id)
        .order_by(BillingSubscription.updated_at.desc())  # type: ignore[attr-defined]
    ).first()
    if subscription is None:
        return True  # onboarding remains usable before checkout is completed
    return subscription.status in ACTIVE_STATES or (
        subscription.status == "past_due"
        and subscription.grace_until is not None
        and subscription.grace_until > utcnow()
    )


def active_invoice_limit(session: Session, merchant_id: uuid.UUID) -> int:
    """Return the effective active-invoice entitlement for a merchant.

    A merchant that has not completed checkout receives the starter trial limit so
    onboarding remains usable without silently granting an unlimited live queue.
    """
    entitlement = session.exec(
        select(BillingEntitlement).where(
            BillingEntitlement.merchant_id == merchant_id,
            BillingEntitlement.feature == "active_invoices",
        )
    ).first()
    if (
        entitlement is not None
        and entitlement.effective_until is not None
        and entitlement.effective_until <= utcnow()
    ):
        return 0
    if entitlement is not None:
        return max(0, entitlement.value)
    return PLAN_CATALOG["starter"][1]


def assert_live_entitled(
    session: Session,
    merchant_id: uuid.UUID,
    *,
    additional_invoices: int = 0,
) -> None:
    """Fail closed when billing is suspended or an invoice cap would be exceeded."""
    if not subscription_is_active(session, merchant_id):
        raise BillingEntitlementError("An active billing subscription is required")
    active_count = session.exec(
        select(func.count(Invoice.id)).where(
            Invoice.merchant_id == merchant_id,
            ~Invoice.status.in_(tuple(TERMINAL_STATUSES)),  # type: ignore[union-attr]
        )
    ).one()
    if int(active_count or 0) + max(0, additional_invoices) > active_invoice_limit(
        session, merchant_id
    ):
        raise BillingEntitlementError("Active invoice entitlement exceeded")


def assert_seat_entitled(session: Session, merchant_id: uuid.UUID) -> None:
    """Enforce included seats for active members plus outstanding invitations."""
    plan_seats = PLAN_CATALOG["starter"][2]
    subscription = session.exec(
        select(BillingSubscription)
        .where(BillingSubscription.merchant_id == merchant_id)
        .order_by(BillingSubscription.updated_at.desc())  # type: ignore[attr-defined]
    ).first()
    if subscription is not None:
        plan = session.get(BillingPlan, subscription.plan_id)
        plan_seats = plan.included_seats if plan is not None else 0
    members = session.exec(
        select(func.count(MerchantMembership.id)).where(
            MerchantMembership.merchant_id == merchant_id,
            MerchantMembership.is_active.is_(True),  # type: ignore[union-attr]
        )
    ).one()
    pending = session.exec(
        select(func.count(MerchantInvitation.id)).where(
            MerchantInvitation.merchant_id == merchant_id,
            MerchantInvitation.accepted_at.is_(None),  # type: ignore[union-attr]
            MerchantInvitation.revoked_at.is_(None),  # type: ignore[union-attr]
        )
    ).one()
    if int(members or 0) + int(pending or 0) >= plan_seats:
        raise BillingEntitlementError("Seat entitlement exceeded")


def create_checkout_subscription(
    session: Session,
    merchant: Merchant,
    plan_slug: str,
    *,
    provider_subscription_id: str | None = None,
) -> BillingSubscription:
    plan = session.exec(
        select(BillingPlan).where(BillingPlan.slug == plan_slug, BillingPlan.is_active.is_(True))  # type: ignore[union-attr]
    ).first()
    if plan is None:
        raise ValueError("Unknown or inactive billing plan")
    existing = session.exec(
        select(BillingSubscription)
        .where(
            BillingSubscription.merchant_id == merchant.id,
            ~BillingSubscription.status.in_(("cancelled", "expired")),  # type: ignore[union-attr]
        )
        .order_by(BillingSubscription.updated_at.desc())  # type: ignore[attr-defined]
    ).first()
    if existing is not None:
        return existing
    row = BillingSubscription(
        merchant_id=merchant.id,
        plan_id=plan.id,
        razorpay_subscription_id=provider_subscription_id,
        status="created",
    )
    session.add(row)
    session.flush()
    return row


def create_provider_subscription(plan: BillingPlan) -> str | None:
    """Create the provider subscription when the platform integration is enabled."""
    if not settings.razorpay_subscriptions_enabled or not plan.razorpay_plan_id:
        return None
    payload = get_razorpay_client().create_subscription(plan_id=plan.razorpay_plan_id)
    provider_id = payload.get("id")
    if not provider_id:
        raise ValueError("Razorpay did not return a subscription ID")
    return str(provider_id)


def apply_subscription_event(
    session: Session,
    raw_body: bytes,
    payload: dict[str, Any],
    *,
    provider_event_id: str,
    signature: str | None,
) -> BillingEvent:
    expected = hmac.new(
        settings.razorpay_webhook_secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    verified = bool(signature) and hmac.compare_digest(expected, signature or "")
    event = BillingEvent(
        provider_event_id=provider_event_id,
        event_type=str(payload.get("event") or payload.get("type") or "unknown"),
        signature_verified=verified,
        payload_hash=hashlib.sha256(raw_body).hexdigest(),
        raw_payload=payload,
    )
    if not verified:
        raise ValueError("Invalid billing webhook signature")
    session.add(event)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.exec(
            select(BillingEvent).where(BillingEvent.provider_event_id == provider_event_id)
        ).one()
        return existing

    entity = payload.get("payload", {}).get("subscription", {}).get("entity", {})
    provider_id = entity.get("id") or payload.get("subscription_id")
    provider_status = str(entity.get("status") or payload.get("status") or "").lower()
    status_value = PROVIDER_STATUS_MAP.get(provider_status, "")
    subscription = session.exec(
        select(BillingSubscription).where(
            BillingSubscription.razorpay_subscription_id == provider_id
        )
    ).first()
    if subscription is not None and status_value:
        subscription.status = status_value
        subscription.updated_at = utcnow()
        if status_value == "past_due":
            subscription.grace_until = utcnow() + timedelta(days=7)
        session.add(subscription)
        merchant = session.get(Merchant, subscription.merchant_id)
        if merchant is not None:
            merchant.status = (
                "active" if subscription_is_active(session, merchant.id) else "suspended"
            )
            session.add(merchant)
            plan = session.get(BillingPlan, subscription.plan_id)
            if plan is not None:
                entitlement = session.exec(
                    select(BillingEntitlement).where(
                        BillingEntitlement.merchant_id == merchant.id,
                        BillingEntitlement.feature == "active_invoices",
                    )
                ).first()
                if entitlement is None:
                    entitlement = BillingEntitlement(
                        merchant_id=merchant.id,
                        feature="active_invoices",
                        value=plan.included_active_invoices,
                    )
                else:
                    entitlement.value = plan.included_active_invoices
                session.add(entitlement)
    event.processed_at = utcnow()
    event.outcome = "applied" if subscription is not None else "unmatched"
    session.add(event)
    return event
