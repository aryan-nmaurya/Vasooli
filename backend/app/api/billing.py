"""Subscription plans, checkout intents and signed billing webhooks."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlmodel import select

from app.core.config import settings
from app.core.db import SessionDep
from app.core.logging import get_logger
from app.models import BillingPlan, BillingSubscription
from app.services.authorization import (
    LiveContext,
    merchant_scope,
    require_live_permission,
    require_live_reauth,
    set_merchant_context,
)
from app.services.billing import (
    BillingEntitlementError,
    apply_subscription_event,
    cancel_subscription,
    checkout_url_for,
    create_checkout_subscription,
    create_provider_subscription,
    discard_abandoned_checkout,
    ensure_plans,
    mandate_verification_paise,
    subscription_state,
    trial_is_available,
    verify_billing_signature,
)
from app.services.plans import PLANS

router = APIRouter(prefix="/api/live/billing", tags=["live-billing"])
log = get_logger("billing")


class CheckoutRequest(BaseModel):
    plan_slug: str = Field(pattern=r"^(starter|growth|scale)$")
    #: Whether this checkout should carry the free trial.
    #:
    #: Asked for rather than assumed. The two paths take different money up front —
    #: a trial takes only the refundable mandate verification and defers the plan
    #: charge, while starting immediately authorises the full plan amount today — so
    #: which one a merchant is agreeing to has to be their explicit choice, made on a
    #: screen that states the figure. It defaults to False so that the ordinary
    #: in-dashboard plan change never quietly turns into a ₹2 mandate flow.
    #:
    #: Requesting a trial does not grant one: `trial_is_available` still decides, so a
    #: returning merchant cannot collect a second trial by asking for it.
    start_trial: bool = False


@router.get("/plans")
def plans() -> list[dict]:
    """Published catalog without the idempotent database write checkout performs.

    Served from `services.plans`, the same definition the public pricing page and the
    entitlement gates read, so the three cannot drift.
    """
    return [
        {
            "slug": plan.slug,
            "version": 1,
            "name": plan.name,
            "description": plan.description,
            "amount_paise": plan.amount_paise,
            "included_active_invoices": plan.included_active_invoices,
            "included_seats": plan.included_seats,
            "highlights": list(plan.highlights),
            "features": sorted(str(f) for f in plan.features),
        }
        for plan in PLANS
    ]


@router.post("/checkout", status_code=status.HTTP_201_CREATED)
def checkout(
    payload: CheckoutRequest,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_reauth("billing.manage"))],
) -> dict:
    try:
        plans = ensure_plans(session)
    except ValueError as exc:
        # `ensure_plans` refuses to silently re-point an immutable plan at a different
        # Razorpay id, which is correct — but it is called before any guarded block,
        # so the refusal reached the merchant as a bare 500 with nothing actionable in
        # it. This is an operator misconfiguration, not something the merchant did, so
        # it is reported as such and logged rather than dressed up as a client error.
        session.rollback()
        log.error("billing.plan_configuration_invalid", error=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Billing is not correctly configured on this deployment. Please contact support.",
        ) from exc
    # `is_active` matters here as well as in the catalog. Without it a retired plan
    # is accepted at this point and then rejected inside
    # `create_checkout_subscription`, which raises ValueError from outside the guarded
    # block below — surfacing to the merchant as an unexplained 500 rather than a
    # readable "unknown plan".
    plan = next((item for item in plans if item.slug == payload.plan_slug and item.is_active), None)
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
    # An `existing` row in `created` has never been authorised: no mandate was
    # confirmed and no money moved. It is an abandoned checkout, not a subscription.
    # Treating it as one dead-ended the merchant — picking a different plan returned
    # "cancel it before changing plans", while the UI only offers Cancel for an ACTIVE
    # subscription, so there was nothing to cancel and no way forward.
    superseded = None
    if existing is not None and existing.plan_id != plan.id and existing.status == "created":
        superseded = existing
        existing = None

    # Assigned in both branches below. Declared here because the re-issue path does not
    # create a provider subscription and so never reaches the decision — leaving it
    # undefined would make reporting the terms a NameError on exactly the path a
    # merchant hits by reloading an abandoned checkout.
    trial_days: int | None = None

    if existing is not None:
        if existing.plan_id != plan.id:
            # A live subscription is a different matter: changing its plan has billing
            # consequences, so it still goes through an explicit cancellation.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "An active subscription already exists; cancel it before changing plans",
            )
        subscription = existing
        # A checkout already in flight: re-issue a fresh provider link rather than a
        # stored one, which Razorpay would have expired.
        checkout_url = checkout_url_for(existing)
        # Report the terms this checkout was CREATED with, not the ones this request
        # asked for. The provider subscription already exists; sending `start_trial`
        # again does not change it, and echoing the request back would tell the
        # merchant they owe ₹2 when the link in front of them charges the full plan.
        if existing.auth_amount_paise > 0:
            trial_days = settings.live_trial_days
    else:
        if superseded is not None:
            # Abandon it at the provider too, or the merchant accumulates half-finished
            # subscriptions on their Razorpay account for every plan they looked at.
            discard_abandoned_checkout(session, superseded)
        try:
            # A merchant who has never subscribed gets the trial, so the first billing
            # cycle starts after it and only the refundable verification amount is
            # taken now. Someone who already had a subscription is returning, and is
            # charged from the start.
            #
            # This asks whether a trial is available, NOT whether one is running.
            # `subscription_state(...).on_trial` only becomes true once the mandate is
            # authenticated, so using it here gave the trial to everyone except the
            # merchant signing up — who was sent to pay the full plan amount instead
            # of ₹2, on the screen that had just promised them a free trial.
            trial_days = (
                settings.live_trial_days
                if payload.start_trial and trial_is_available(session, context.merchant.id)
                else None
            )
            provider_id, checkout_url = create_provider_subscription(plan, trial_days=trial_days)
        except Exception as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        try:
            subscription = create_checkout_subscription(
                session,
                context.merchant,
                payload.plan_slug,
                provider_subscription_id=provider_id,
            )
        except ValueError as exc:
            # Reachable if the plan is retired between the lookup above and here.
            # A 500 tells the merchant nothing and looks like an outage.
            session.rollback()
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    # `commit()` expires every instance above, so building the response re-reads
    # them — in a NEW transaction, where the transaction-local `app.merchant_id`
    # from `set_merchant_context` is already gone. RLS then matches no rows and
    # SQLAlchemy reports the subscription it just wrote as deleted. Holding the
    # tenant across the commit is what `merchant_scope` is for; the alternative,
    # reading the fields into locals beforehand, breaks again the next time anyone
    # touches an ORM attribute down here.
    with merchant_scope(session, context.merchant.id):
        session.commit()
        stored_plan = session.get(BillingPlan, subscription.plan_id)
        subscription_id = str(subscription.id)
        subscription_status = subscription.status
        amount_paise = stored_plan.amount_paise if stored_plan else None
        provider_plan_id = stored_plan.razorpay_plan_id if stored_plan else None
    return {
        "subscription_id": subscription_id,
        "status": subscription_status,
        "plan": payload.plan_slug,
        "amount_paise": amount_paise,
        "provider_plan_id": provider_plan_id,
        "checkout_required": True,
        # What this checkout actually takes now, so the UI never has to re-derive it.
        # `trial_days` is the server's decision, not the request's: asking for a trial
        # a merchant is not eligible for returns a normal paid checkout, and the
        # response says so rather than letting the page keep promising ₹2.
        "trial_applied": trial_days is not None,
        "trial_days": trial_days,
        "amount_due_now_paise": (
            settings.trial_auth_amount_paise if trial_days is not None else amount_paise
        ),
        # Where the merchant authorises the mandate and pays. None when Razorpay
        # subscriptions are not configured, which the UI reports rather than
        # pretending a checkout exists.
        "checkout_url": checkout_url,
    }


@router.get("/subscription")
def subscription(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("billing.read"))],
) -> dict:
    """Plan, status, days remaining and whether automation is paused.

    Always returns an object. A merchant on trial has no subscription row, and an
    endpoint that answered `null` there forced every caller to re-derive the trial
    rules for itself.
    """
    state = subscription_state(session, context.merchant.id)
    body = state.to_dict()
    # What the next checkout will actually take up front, and whether it carries the
    # trial. The billing page states both to the merchant before they authorise
    # anything, and it used to infer them from `on_trial` — which is false until the
    # mandate is confirmed, so the ₹2 explanation never appeared for the one merchant
    # it was written for. Answered by the server so the promise cannot drift from what
    # the provider call really sends.
    body["mandate_verification_paise"] = mandate_verification_paise(session, context.merchant.id)
    body["trial_available"] = trial_is_available(session, context.merchant.id)
    body["trial_days"] = settings.live_trial_days
    if state.subscription is not None:
        body["id"] = str(state.subscription.id)
        body["checkout_url"] = checkout_url_for(state.subscription) if not state.is_active else None
    return body


@router.post("/cancel")
def cancel(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_reauth("billing.manage"))],
) -> dict:
    """Stop recurring billing at the end of the paid period.

    Re-authentication is required: cancelling has a financial consequence, and a
    borrowed session should not be able to end someone's service.
    """
    try:
        state = cancel_subscription(session, context.merchant.id)
    except BillingEntitlementError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()
    return state.to_dict()


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
