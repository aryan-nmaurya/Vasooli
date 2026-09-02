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
from app.services.plans import PLANS, TRIAL_PLAN, Feature, Plan, plan_for

#: Kept as the legacy (amount, invoices, seats) shape that older call sites read,
#: but derived from `plans.PLANS` so the catalogue has exactly one definition.
PLAN_CATALOG = {
    plan.slug: (plan.amount_paise, plan.included_active_invoices, plan.included_seats)
    for plan in PLANS
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


def create_provider_subscription(plan: BillingPlan) -> tuple[str | None, str | None]:
    """Create the provider subscription, returning its id and hosted checkout URL.

    `short_url` is where the merchant actually authorises the mandate and pays. It is
    returned rather than stored: Razorpay expires these, so a link persisted now and
    opened next week sends the merchant to a dead page.
    """
    if not settings.razorpay_subscriptions_enabled or not plan.razorpay_plan_id:
        return None, None
    payload = get_razorpay_client().create_subscription(plan_id=plan.razorpay_plan_id)
    provider_id = payload.get("id")
    if not provider_id:
        raise ValueError("Razorpay did not return a subscription ID")
    return str(provider_id), payload.get("short_url")


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

    if row is None or row.status in _DEAD_STATES:
        # No live subscription: the merchant is on trial, or the trial has run out.
        # A cancelled subscription still shows its plan so the UI can offer that tier
        # back, but grants nothing.
        plan = plan_for(_plan_slug(session, row)) if row is not None else TRIAL_PLAN
        end = trial_ends_at(merchant) if merchant is not None else None
        on_trial = row is None and end is not None and end > now
        days = _days_between(now, end) if on_trial else 0
        if on_trial:
            reason = None
        elif row is not None and row.status == "cancelled":
            reason = "Your subscription was cancelled. Renew to resume automation."
        elif row is not None:
            reason = "Your subscription has expired. Renew to resume automation."
        else:
            reason = "Your free trial has ended. Choose a plan to resume automation."
        return SubscriptionState(
            subscription=row,
            plan=TRIAL_PLAN if on_trial else plan,
            status="trialing" if on_trial else (row.status if row else "trial_expired"),
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
        get_razorpay_client().cancel_subscription(
            row.razorpay_subscription_id, cancel_at_cycle_end=not immediate
        )

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
        payload = get_razorpay_client().fetch_subscription(subscription.razorpay_subscription_id)
    except Exception:
        return None
    return payload.get("short_url")
