"""Razorpay subscription state, plans and enforceable entitlements."""

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.config import settings
from app.core.constants import TERMINAL_STATUSES
from app.core.logging import get_logger
from app.integrations.razorpay_client import (
    RazorpayError,
    RazorpayPermanentError,
    get_billing_client,
)
from app.integrations.razorpay_signature import verify_signature
from app.models import (
    BillingEntitlement,
    BillingEvent,
    BillingPlan,
    BillingRefund,
    BillingSubscription,
    Invoice,
    Merchant,
    MerchantInvitation,
    MerchantMembership,
)
from app.services.plans import PLANS, TRIAL_PLAN, Feature, Plan, plan_for

#: Kept as the legacy (amount, invoices, seats) shape that older call sites read,
#: but derived from `plans.PLANS` so the catalogue has exactly one definition.
PLAN_CATALOG = {
    plan.slug: (plan.amount_paise, plan.included_active_invoices, plan.included_seats)
    for plan in PLANS
}

log = get_logger("billing")

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


class BillingProviderError(RuntimeError):
    """The payment provider refused an operation this service asked it to perform.

    Raised so routers can report a provider refusal without importing the Razorpay
    layer themselves — translating the integration's errors into the domain's is this
    service's job, and `test_api_does_not_call_integrations_directly` enforces it.
    """


def verify_billing_signature(raw_body: bytes, signature: str | None) -> bool:
    """Subscription webhooks are signed by the BILLING account, not the demo one."""
    return verify_signature(raw_body, signature, settings.effective_billing_webhook_secret)


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
        merchant = session.get(Merchant, merchant_id)
        if merchant is None:
            return False
        raw_end = (merchant.onboarding_state or {}).get("trial_ends_at")
        try:
            trial_end = datetime.fromisoformat(raw_end) if raw_end else None
        except (TypeError, ValueError):
            trial_end = None
        trial_end = trial_end or (merchant.created_at + timedelta(days=settings.live_trial_days))
        return trial_end > utcnow()
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
    """Fail closed when billing is suspended or an invoice cap would be exceeded.

    The message comes from `subscription_state`, so a merchant is told what actually
    stopped them — trial ended, payment failed, cancelled — instead of one generic
    line that fits none of those cases.
    """
    assert_write_allowed(session, merchant_id)
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
    state = subscription_state(session, merchant_id)
    plan_seats = state.plan.included_seats
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
    used = int(members or 0) + int(pending or 0)
    if used >= plan_seats:
        seat_word = "seat" if plan_seats == 1 else "seats"
        raise BillingEntitlementError(
            f"{state.plan.name} includes {plan_seats} {seat_word} and {used} are in use. "
            "Upgrade your plan to invite more people."
        )


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


def create_provider_subscription(
    plan: BillingPlan, *, trial_days: int | None = None
) -> tuple[str | None, str | None]:
    """Create the provider subscription, returning its id and hosted checkout URL.

    `short_url` is where the merchant authorises the mandate and pays. It is returned
    rather than stored: Razorpay expires these, so a link persisted now and opened
    next week sends the merchant to a dead page.

    When `trial_days` is given the first billing cycle is pushed that far out, so the
    plan amount is not charged during the trial, and a small verification amount is
    added to the authorisation instead. That charge is what makes the mandate real —
    a bank or UPI app will not confirm a recurring debit approval without one — and
    it is refunded as soon as the subscription reports itself authenticated.

    Doing it this way means the first post-trial charge runs against an instrument
    that has already been proven to work, rather than discovering on day eight that
    the mandate was never valid.
    """
    if not settings.razorpay_subscriptions_enabled or not plan.razorpay_plan_id:
        return None, None

    start_at = None
    auth_amount = None
    if trial_days:
        start_at = int((utcnow() + timedelta(days=trial_days)).timestamp())
        auth_amount = settings.trial_auth_amount_paise

    payload = get_billing_client().create_subscription(
        plan_id=plan.razorpay_plan_id, start_at=start_at, auth_amount_paise=auth_amount
    )
    provider_id = payload.get("id")
    if not provider_id:
        raise ValueError("Razorpay did not return a subscription ID")
    return str(provider_id), payload.get("short_url")


def refund_mandate_verification(session: Session, subscription: BillingSubscription) -> str | None:
    """Return the mandate-verification charge, exactly once.

    Called when a subscription reports itself authenticated. That webhook can be
    delivered more than once, and Razorpay will happily issue a second refund for the
    same payment — which returns real money twice. The stored refund id is what makes
    this safe to call repeatedly.

    A failure here is logged and left for the next delivery rather than raised: the
    merchant's subscription is validly authenticated either way, and refusing the
    webhook over an unrefunded two rupees would leave their billing state stuck.
    """
    if subscription.auth_refund_id:
        return subscription.auth_refund_id
    if not subscription.auth_payment_id or subscription.auth_amount_paise <= 0:
        return None

    try:
        result = get_billing_client().refund_payment(
            subscription.auth_payment_id,
            amount_paise=subscription.auth_amount_paise,
            notes={"reason": "mandate verification refund", "subscription": str(subscription.id)},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "billing.auth_refund_failed",
            subscription_id=str(subscription.id),
            payment_id=subscription.auth_payment_id,
            error=str(exc)[:200],
        )
        return None

    refund_id = str(result.get("id") or "")
    if not refund_id:
        return None

    subscription.auth_refund_id = refund_id
    session.add(subscription)
    session.add(
        BillingRefund(
            merchant_id=subscription.merchant_id,
            provider_refund_id=refund_id,
            amount_paise=subscription.auth_amount_paise,
            status=str(result.get("status") or "processed"),
        )
    )
    log.info(
        "billing.auth_refunded",
        subscription_id=str(subscription.id),
        amount_paise=subscription.auth_amount_paise,
    )
    return refund_id


def apply_subscription_event(
    session: Session,
    raw_body: bytes,
    payload: dict[str, Any],
    *,
    provider_event_id: str,
    signature: str | None,
) -> BillingEvent:
    expected = hmac.new(
        settings.effective_billing_webhook_secret.encode(), raw_body, hashlib.sha256
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

        # The mandate is now proven. Record the payment that proved it, then give it
        # back — the merchant agreed to a free trial, so the verification charge must
        # not stay taken. `refund_mandate_verification` is safe to reach more than
        # once; this webhook can be redelivered and a second refund would return real
        # money twice.
        if status_value in _LIVE_STATES:
            payment_id = entity.get("payment_id") or (
                payload.get("payload", {}).get("payment", {}).get("entity", {}).get("id")
            )
            # Razorpay does not put a payment entity on `subscription.authenticated`.
            # Verified against a real production event: both lookups above come back
            # empty, so `auth_payment_id` stayed null, the refund guard returned early,
            # and the merchant's ₹2 was captured and never returned. The payment id
            # lives on the subscription's first invoice, so ask for it.
            #
            # Best-effort by design: a provider hiccup here must not fail a webhook
            # that is otherwise valid, and the reconciliation job re-runs this path.
            if not payment_id and not subscription.auth_payment_id and provider_id:
                try:
                    payment_id = get_billing_client().find_auth_payment_id(str(provider_id))
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "billing.auth_payment_lookup_failed",
                        subscription_id=str(subscription.id),
                        error=str(exc)[:200],
                    )
            if payment_id and not subscription.auth_payment_id:
                subscription.auth_payment_id = str(payment_id)
                if subscription.auth_amount_paise <= 0:
                    subscription.auth_amount_paise = settings.trial_auth_amount_paise
            session.add(subscription)
            refund_mandate_verification(session, subscription)

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


# ===========================================================================
# Subscription state, trial, and the read-only gate.
#
# The product rule, decided deliberately: a lapsed subscription pauses the
# AUTOMATION, never the merchant's access to their own records. Vasooli holds
# receivables, payments, disputes and an audit trail that a business may need for
# its own filings; withholding those to apply payment pressure would be both a
# trust problem and, plausibly, a compliance one. So every read stays open and every
# export stays open, while the things that cost money or touch a customer stop.
# ===========================================================================

#: Statuses where the subscription is paid and current.
_LIVE_STATES = frozenset({"active", "authenticated"})
#: Statuses that end the relationship. A merchant here has no plan at all.
_DEAD_STATES = frozenset({"cancelled", "expired"})


@dataclass(frozen=True)
class SubscriptionState:
    """Everything the UI and the gates need, resolved once."""

    #: None while the merchant is on trial and has never subscribed.
    subscription: BillingSubscription | None
    plan: Plan
    status: str
    #: True when automation may run and billable writes are permitted.
    is_active: bool
    on_trial: bool
    #: Whole days remaining in the current paid period or trial; 0 once elapsed.
    days_remaining: int
    period_end: datetime | None
    cancel_at_period_end: bool
    #: Why writes are refused, or None when they are permitted. Set only when the
    #: subscription is genuinely inactive — a grace period is a warning, not a block.
    paused_reason: str | None
    #: Something the merchant should act on while service continues, e.g. a failed
    #: card inside the grace window. Never blocks; the banner shows it.
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "plan": {
                "slug": self.plan.slug,
                "name": self.plan.name,
                "amount_paise": self.plan.amount_paise,
                "included_active_invoices": self.plan.included_active_invoices,
                "included_seats": self.plan.included_seats,
                "features": sorted(str(f) for f in self.plan.features),
            },
            "is_active": self.is_active,
            "on_trial": self.on_trial,
            "days_remaining": self.days_remaining,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "cancel_at_period_end": self.cancel_at_period_end,
            "paused_reason": self.paused_reason,
            "warning": self.warning,
            "provider_subscription_id": (
                self.subscription.razorpay_subscription_id if self.subscription else None
            ),
        }


def discard_abandoned_checkout(session: Session, row: BillingSubscription) -> None:
    """Retire a `created` subscription the merchant walked away from.

    Only ever called for a row still in `created`, which means Razorpay never reported
    an authorised mandate: no instrument was confirmed and no money moved, so there is
    nothing to refund and nobody to notify.

    A provider-side failure is logged, not raised. The local row must still be retired
    or the merchant stays stuck on the plan they abandoned — and an orphan left in
    `created` at Razorpay never charges anyone, which is the same state it was already
    in. That is the safe direction to fail in.
    """
    if row.razorpay_subscription_id and settings.razorpay_subscriptions_enabled:
        try:
            get_billing_client().cancel_subscription(
                row.razorpay_subscription_id, cancel_at_cycle_end=False
            )
        except Exception as exc:
            log.warning(
                "billing.abandoned_checkout_not_cancelled",
                subscription_id=row.razorpay_subscription_id,
                error=str(exc),
            )
    row.status = "cancelled"
    row.updated_at = utcnow()
    session.add(row)
    session.flush()


def trial_is_available(session: Session, merchant_id: uuid.UUID) -> bool:
    """Whether the next checkout should carry the trial and the mandate charge.

    The trial belongs to a merchant's FIRST subscription, and nothing else. Checkout
    used to ask `subscription_state(...).on_trial` instead, which is exactly backwards:
    `on_trial` is true only once Razorpay reports the mandate `authenticated`, so it is
    false for precisely the merchant who has never subscribed. The result was that a
    new merchant got no trial and no ₹2 verification — they were sent to authorise the
    full plan amount immediately, on the very screen that promises a free trial.

    "Has ever had a row" rather than "has one now": a merchant whose subscription was
    cancelled is returning, not new, and must not collect a fresh trial each time they
    resubscribe. Rows in dead states therefore still count.
    """
    return (
        session.exec(
            select(BillingSubscription.id).where(BillingSubscription.merchant_id == merchant_id)
        ).first()
        is None
    )


def mandate_verification_paise(session: Session, merchant_id: uuid.UUID) -> int | None:
    """What the next checkout will actually take now, or None if it takes nothing.

    The billing page states this amount to the merchant before they authorise it, and
    stating it wrongly is a promise about their money. Derived here so the UI cannot
    drift from what `create_provider_subscription` really sends.
    """
    if not trial_is_available(session, merchant_id):
        return None
    if not settings.razorpay_subscriptions_enabled:
        return None
    return settings.trial_auth_amount_paise


def trial_ends_at(merchant: Merchant) -> datetime:
    """When this merchant's free trial expires.

    Read from `onboarding_state` when present so an extension granted by support is
    honoured, and otherwise computed from signup. Computing it rather than requiring
    the field means merchants created before trials existed still get one.
    """
    raw = (merchant.onboarding_state or {}).get("trial_ends_at")
    if raw:
        try:
            parsed = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return merchant.created_at + timedelta(days=settings.live_trial_days)


def start_trial(merchant: Merchant) -> None:
    """Stamp the trial window at signup so it cannot drift with `created_at` edits."""
    state = dict(merchant.onboarding_state or {})
    state.setdefault(
        "trial_ends_at", (utcnow() + timedelta(days=settings.live_trial_days)).isoformat()
    )
    merchant.onboarding_state = state


def _days_between(now: datetime, end: datetime | None) -> int:
    if end is None:
        return 0
    remaining = end - now
    return max(0, ceil(remaining.total_seconds() / 86_400))


def subscription_state(session: Session, merchant_id: uuid.UUID) -> SubscriptionState:
    """Resolve plan, status and remaining days in one place.

    Every gate and the billing UI read this, so there is a single answer to "is this
    workspace paid up?" rather than several that can disagree.
    """
    now = utcnow()
    merchant = session.get(Merchant, merchant_id)
    row = session.exec(
        select(BillingSubscription)
        .where(BillingSubscription.merchant_id == merchant_id)
        .order_by(BillingSubscription.updated_at.desc())  # type: ignore[attr-defined]
    ).first()

    # The seeded demo is never billed. It has no subscription and never will, and
    # every gate below would otherwise lock a reviewer out of the one workspace that
    # exists to be looked at.
    if merchant is not None and merchant.is_demo:
        end = trial_ends_at(merchant)
        return SubscriptionState(
            subscription=None,
            plan=TRIAL_PLAN,
            status="trialing",
            is_active=True,
            on_trial=True,
            days_remaining=_days_between(now, end),
            period_end=end,
            cancel_at_period_end=False,
            paused_reason=None,
        )

    if row is None or row.status in _DEAD_STATES:
        # No live subscription: the merchant is on trial, or the trial has run out.
        # A cancelled subscription still shows its plan so the UI can offer that tier
        # back, but grants nothing.
        plan = plan_for(_plan_slug(session, row)) if row is not None else TRIAL_PLAN
        end = trial_ends_at(merchant) if merchant is not None else None
        # The trial is something a merchant enters by confirming a payment mandate,
        # not something registration hands out. Signing up used to grant a fully
        # active workspace before any card was seen, so the trial was really an
        # unauthenticated free tier — and the first time a payment instrument was
        # tested was the day the trial ended and the first real charge failed.
        #
        # `authenticated` is the status Razorpay reports once the mandate is
        # confirmed, so a merchant inside their trial window reaches the branch below
        # rather than this one. Landing here with no row means they have not paid.
        on_trial = False
        days = _days_between(now, end) if on_trial else 0
        if on_trial:
            reason = None
        elif row is not None and row.status == "cancelled":
            reason = "Your subscription was cancelled. Renew to resume automation."
        elif row is not None:
            reason = "Your subscription has expired. Renew to resume automation."
        else:
            reason = (
                "Choose a plan and confirm payment to activate your workspace. "
                "Your trial starts once the mandate is confirmed."
            )
        return SubscriptionState(
            subscription=row,
            plan=TRIAL_PLAN if on_trial else plan,
            status="trialing" if on_trial else (row.status if row else "awaiting_payment"),
            is_active=on_trial,
            on_trial=on_trial,
            days_remaining=days,
            period_end=end if on_trial else (row.current_period_end if row else None),
            cancel_at_period_end=bool(row.cancel_at_period_end) if row else False,
            paused_reason=reason,
        )

    plan = plan_for(_plan_slug(session, row))
    in_grace = row.status == "past_due" and row.grace_until is not None and row.grace_until > now
    is_active = row.status in _LIVE_STATES or in_grace

    # The trial lives here rather than in the no-subscription branch above. Razorpay
    # reports `authenticated` once the mandate is confirmed and before the first plan
    # charge, which is exactly the trial window: the merchant has proven an instrument
    # works, and has not yet been billed for the plan.
    #
    # Keeping it here is what lets the trial be a real trial. Previously it hung off
    # having no subscription at all, so it was an unauthenticated free tier and the
    # first time a card was tested was the day it ended — the worst possible moment to
    # discover the mandate was never valid.
    trial_end = trial_ends_at(merchant) if merchant is not None else None
    on_trial = row.status == "authenticated" and trial_end is not None and trial_end > now
    if on_trial:
        return SubscriptionState(
            subscription=row,
            plan=plan,
            status="trialing",
            is_active=True,
            on_trial=True,
            days_remaining=_days_between(now, trial_end),
            period_end=trial_end,
            cancel_at_period_end=bool(row.cancel_at_period_end),
            paused_reason=None,
        )

    end = row.grace_until if in_grace else row.current_period_end
    warning = None
    if row.status in _LIVE_STATES:
        reason = None
    elif in_grace:
        # Service continues: that is the entire point of a grace period. The merchant
        # is warned, not stopped — a retryable card failure must not read as an outage.
        reason = None
        warning = "Your last payment failed. Update your payment method to avoid interruption."
    elif row.status == "past_due":
        reason = "Your last payment failed and the grace period has ended."
    elif row.status == "paused":
        reason = "Your subscription is paused. Resume it to restart automation."
    else:
        reason = "Your subscription is not active. Renew to resume automation."
    return SubscriptionState(
        subscription=row,
        plan=plan,
        status=row.status,
        is_active=is_active,
        on_trial=False,
        days_remaining=_days_between(now, end),
        period_end=end,
        cancel_at_period_end=bool(row.cancel_at_period_end),
        paused_reason=reason,
        warning=warning,
    )


def _plan_slug(session: Session, row: BillingSubscription | None) -> str:
    if row is None:
        return TRIAL_PLAN.slug
    plan = session.get(BillingPlan, row.plan_id)
    return plan.slug if plan is not None else TRIAL_PLAN.slug


def write_paused_reason(session: Session, merchant_id: uuid.UUID) -> str | None:
    """Why billable writes are refused, or None when they are permitted."""
    return subscription_state(session, merchant_id).paused_reason


def assert_write_allowed(session: Session, merchant_id: uuid.UUID) -> None:
    """Gate every mutation that costs money or reaches a customer.

    Reads and exports deliberately do not call this.
    """
    reason = write_paused_reason(session, merchant_id)
    if reason is not None:
        raise BillingEntitlementError(reason)


def assert_feature_entitled(session: Session, merchant_id: uuid.UUID, feature: Feature) -> None:
    """Gate a capability that belongs to a higher tier.

    Separate from `assert_write_allowed`: one asks "is this workspace paid up?", the
    other "does this plan include that?". Conflating them produced the wrong message
    — a Starter merchant told to renew a subscription that was already current.
    """
    state = subscription_state(session, merchant_id)
    if feature in state.plan.features:
        return
    required = next((p for p in PLANS if feature in p.features), None)
    upgrade_to = required.name if required is not None else "a higher plan"
    raise BillingEntitlementError(
        f"{state.plan.name} does not include this feature. Upgrade to {upgrade_to} to use it."
    )


def cancel_subscription(
    session: Session, merchant_id: uuid.UUID, *, immediate: bool = False
) -> SubscriptionState:
    """Stop recurring billing.

    Defaults to end-of-period: the merchant paid for this month and keeps it. The
    provider is told first — if that call fails the local row is left alone, because
    a workspace that believes it is cancelled while Razorpay keeps charging is the
    one failure mode here that costs the merchant money.
    """
    row = session.exec(
        select(BillingSubscription)
        .where(
            BillingSubscription.merchant_id == merchant_id,
            ~BillingSubscription.status.in_(tuple(_DEAD_STATES)),  # type: ignore[union-attr]
        )
        .order_by(BillingSubscription.updated_at.desc())  # type: ignore[attr-defined]
    ).first()
    if row is None:
        raise BillingEntitlementError("There is no active subscription to cancel")

    if row.razorpay_subscription_id and settings.razorpay_subscriptions_enabled:
        try:
            get_billing_client().cancel_subscription(
                row.razorpay_subscription_id, cancel_at_cycle_end=not immediate
            )
        except RazorpayPermanentError as exc:
            # Cancel-at-cycle-end needs a cycle to end. A subscription that has not
            # started billing — `created`, or `authenticated` for the whole of a
            # trial — has none, and Razorpay refuses with "Subscription cannot be
            # cancelled since no billing cycle is going on". That refusal escaped as
            # a bare 500, so cancelling during a trial simply did not work: exactly
            # the window the activation screen promises you can leave in.
            #
            # There is nothing to preserve in that state. No period has been paid
            # for, so ending it now costs the merchant nothing and is what they
            # asked for.
            if immediate or "billing cycle" not in str(exc).casefold():
                raise BillingProviderError(str(exc)) from exc
            try:
                get_billing_client().cancel_subscription(
                    row.razorpay_subscription_id, cancel_at_cycle_end=False
                )
            except RazorpayError as inner:
                raise BillingProviderError(str(inner)) from inner
            immediate = True

    if immediate:
        row.status = "cancelled"
        row.cancel_at_period_end = False
    else:
        row.cancel_at_period_end = True
    row.updated_at = utcnow()
    session.add(row)
    session.flush()
    return subscription_state(session, merchant_id)


def checkout_url_for(subscription: BillingSubscription) -> str | None:
    """A live hosted-checkout URL for a subscription already created.

    Fetched on demand rather than stored: Razorpay expires these links, so one
    persisted at creation and served a week later sends the merchant to a dead page.
    A provider failure returns None instead of raising — the billing page must still
    render what is owed even when Razorpay is briefly unreachable.
    """
    if not subscription.razorpay_subscription_id or not settings.razorpay_subscriptions_enabled:
        return None
    try:
        payload = get_billing_client().fetch_subscription(subscription.razorpay_subscription_id)
    except Exception:
        return None
    return payload.get("short_url")
