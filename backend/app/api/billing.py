"""Subscription plans, checkout intents and signed billing webhooks."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlmodel import select

from app.core.db import SessionDep
from app.models import BillingPlan, BillingSubscription
from app.services.authorization import (
    LiveContext,
    require_live_permission,
    require_live_reauth,
    set_merchant_context,
)
from app.services.billing import (
    apply_subscription_event,
    create_checkout_subscription,
    create_provider_subscription,
    ensure_plans,
    verify_billing_signature,
)

router = APIRouter(prefix="/api/live/billing", tags=["live-billing"])


class CheckoutRequest(BaseModel):
    plan_slug: str = Field(pattern=r"^(starter|growth|scale)$")


@router.get("/plans")
def plans(session: SessionDep) -> list[dict]:
    rows = ensure_plans(session)
    session.commit()
    return [
        {
            "slug": row.slug,
            "version": row.version,
            "name": row.name,
            "amount_paise": row.amount_paise,
            "included_active_invoices": row.included_active_invoices,
            "included_seats": row.included_seats,
        }
        for row in rows
        if row.is_active
    ]


@router.post("/checkout", status_code=status.HTTP_201_CREATED)
def checkout(
    payload: CheckoutRequest,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_reauth("billing.manage"))],
) -> dict:
    plans = ensure_plans(session)
    plan = next((item for item in plans if item.slug == payload.plan_slug), None)
    if plan is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown billing plan")
    existing = session.exec(
        select(BillingSubscription)
        .where(
            BillingSubscription.merchant_id == context.merchant.id,
            ~BillingSubscription.status.in_(("cancelled", "expired")),  # type: ignore[union-attr]
        )
        .order_by(BillingSubscription.updated_at.desc())  # type: ignore[attr-defined]
    ).first()
    if existing is not None:
        if existing.plan_id != plan.id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "An active checkout already exists; cancel it before changing plans",
            )
        subscription = existing
    else:
        try:
            provider_id = create_provider_subscription(plan)
        except Exception as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        subscription = create_checkout_subscription(
            session,
            context.merchant,
            payload.plan_slug,
            provider_subscription_id=provider_id,
        )
    session.commit()
    plan = session.get(BillingPlan, subscription.plan_id)
    return {
        "subscription_id": str(subscription.id),
        "status": subscription.status,
        "plan": payload.plan_slug,
        "amount_paise": plan.amount_paise if plan else None,
        "provider_plan_id": plan.razorpay_plan_id if plan else None,
        "checkout_required": True,
    }


@router.get("/subscription")
def subscription(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("billing.read"))],
) -> dict | None:
    row = session.exec(
        select(BillingSubscription)
        .where(BillingSubscription.merchant_id == context.merchant.id)
        .order_by(BillingSubscription.updated_at.desc())  # type: ignore[attr-defined]
    ).first()
    if row is None:
        return None
    return {
        "id": str(row.id),
        "plan_id": str(row.plan_id),
        "provider_subscription_id": row.razorpay_subscription_id,
        "status": row.status,
        "grace_until": row.grace_until.isoformat() if row.grace_until else None,
        "cancel_at_period_end": row.cancel_at_period_end,
    }


@router.post("/webhook")
async def webhook(
    request: Request,
    session: SessionDep,
    x_razorpay_signature: Annotated[str | None, Header(alias="X-Razorpay-Signature")] = None,
    x_razorpay_event_id: Annotated[str | None, Header(alias="X-Razorpay-Event-Id")] = None,
) -> dict[str, str]:
    raw = await request.body()
    if not x_razorpay_event_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing webhook event id")
    if not verify_billing_signature(raw, x_razorpay_signature):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook signature")
    try:
        payload = json.loads(raw)
        # Platform webhooks have no merchant header. After signature verification,
        # temporarily allow provider-ID lookup, then pin all writes to that merchant.
        session.exec(text("SELECT set_config('app.webhook_mode', 'true', true)"))
        entity = payload.get("payload", {}).get("subscription", {}).get("entity", {})
        provider_id = entity.get("id") or payload.get("subscription_id")
        if provider_id:
            existing = session.exec(
                select(BillingSubscription).where(
                    BillingSubscription.razorpay_subscription_id == str(provider_id)
                )
            ).first()
            if existing is not None:
                set_merchant_context(session, existing.merchant_id)
        event = apply_subscription_event(
            session,
            raw,
            payload,
            provider_event_id=x_razorpay_event_id,
            signature=x_razorpay_signature,
        )
        session.commit()
    except (ValueError, json.JSONDecodeError) as exc:
        session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"status": "duplicate" if event.outcome is None else event.outcome}
