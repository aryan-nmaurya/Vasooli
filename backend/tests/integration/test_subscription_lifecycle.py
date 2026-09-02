"""Subscription lifecycle: trial, lapse, read-only, tiers and cancellation.

The product rule these pin: a lapsed subscription pauses the AUTOMATION, never the
merchant's access to their own records. Vasooli holds receivables, disputes and an
audit trail a business may need for its own filings, so reads and exports stay open
while anything that costs money or reaches a customer stops.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.models import BillingSubscription, Merchant
from app.services.billing import (
    BillingEntitlementError,
    assert_feature_entitled,
    assert_seat_entitled,
    assert_write_allowed,
    cancel_subscription,
    ensure_plans,
    subscription_state,
)
from app.services.plans import GROWTH, SCALE, STARTER, Feature


@pytest.fixture
def live_merchant(session):
    m = Merchant(
        name="Subscriber Ltd",
        contact_email="ops@subscriber.example",
        is_demo=False,
        mode="live",
        onboarding_state={},
    )
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


def subscribe(session, merchant, slug: str, *, status: str = "active", **kw):
    plan = next(p for p in ensure_plans(session) if p.slug == slug)
    row = BillingSubscription(merchant_id=merchant.id, plan_id=plan.id, status=status, **kw)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.fixture
def seat_holder(session, live_merchant):
    """One active member, so the seat count under test is actually occupied."""
    from app.models import MerchantMembership, User
    from app.services.auth import bootstrap_roles

    user = User(email="owner@subscriber.example", password_hash="x", is_active=True)
    session.add(user)
    session.commit()
    session.refresh(user)
    role = bootstrap_roles(session, live_merchant)["owner"]
    session.commit()
    membership = MerchantMembership(
        merchant_id=live_merchant.id, user_id=user.id, role_id=role.id, is_active=True
    )
    session.add(membership)
    session.commit()
    return membership


def set_trial(session, merchant, *, days_from_now: float):
    merchant.onboarding_state = {
        "trial_ends_at": (datetime.now(UTC) + timedelta(days=days_from_now)).isoformat()
    }
    session.add(merchant)
    session.commit()


# ===========================================================================
# Trial
# ===========================================================================


def test_a_merchant_who_has_not_paid_gets_nothing(session, live_merchant):
    """Registration alone must not open a workspace.

    It used to: signing up granted a fully active trial before any payment instrument
    had been seen, so the first time a card was tested was the day the trial ended and
    the first real charge failed. The trial now begins when the mandate is confirmed.
    """
    set_trial(session, live_merchant, days_from_now=settings.live_trial_days)
    state = subscription_state(session, live_merchant.id)

    assert state.is_active is False
    assert state.on_trial is False
    assert state.status == "awaiting_payment"
    assert "confirm payment" in (state.paused_reason or "")


def test_confirming_the_mandate_starts_the_trial(session, live_merchant):
    """`authenticated` is Razorpay's "mandate confirmed, plan not yet charged"."""
    set_trial(session, live_merchant, days_from_now=settings.live_trial_days)
    subscribe(session, live_merchant, "starter", status="authenticated")
    state = subscription_state(session, live_merchant.id)

    assert state.on_trial is True
    assert state.is_active is True
    assert state.plan.slug == STARTER.slug
    assert state.status == "trialing"
    assert state.paused_reason is None
    assert state.days_remaining == settings.live_trial_days


def test_the_trial_counts_down(session, live_merchant):
    set_trial(session, live_merchant, days_from_now=2.5)
    subscribe(session, live_merchant, "starter", status="authenticated")
    assert subscription_state(session, live_merchant.id).days_remaining == 3


def test_an_expired_trial_stops_being_a_trial(session, live_merchant):
    """Past the window the merchant is billed, not trialing.

    `authenticated` still counts as live: Razorpay charges the first cycle at trial
    end, and refusing service in the gap between "trial over" and "first charge
    settled" would suspend a merchant who has done nothing wrong.
    """
    set_trial(session, live_merchant, days_from_now=-1)
    subscribe(session, live_merchant, "starter", status="authenticated")
    state = subscription_state(session, live_merchant.id)

    assert state.on_trial is False
    assert state.status == "authenticated"


def test_an_unpaid_merchant_past_the_window_is_still_refused(session, live_merchant):
    set_trial(session, live_merchant, days_from_now=-1)
    state = subscription_state(session, live_merchant.id)

    assert state.is_active is False
    assert state.days_remaining == 0
    assert "confirm payment" in (state.paused_reason or "")


# ===========================================================================
# The read-only gate
# ===========================================================================


def test_writes_are_refused_before_the_mandate_is_confirmed(session, live_merchant):
    set_trial(session, live_merchant, days_from_now=-1)
    with pytest.raises(BillingEntitlementError) as exc:
        assert_write_allowed(session, live_merchant.id)
    assert "confirm payment" in str(exc.value)


def test_writes_are_allowed_on_an_active_subscription(session, live_merchant):
    subscribe(session, live_merchant, "starter")
    assert_write_allowed(session, live_merchant.id)  # must not raise


def test_a_failed_payment_keeps_working_through_the_grace_period(session, live_merchant):
    subscribe(
        session,
        live_merchant,
        "growth",
        status="past_due",
        grace_until=datetime.now(UTC) + timedelta(days=3),
    )
    state = subscription_state(session, live_merchant.id)

    assert state.is_active is True, "grace exists so a retryable card failure is not an outage"
    assert state.paused_reason is None, "a grace period warns; it does not block"
    assert "payment failed" in (state.warning or "")
    assert_write_allowed(session, live_merchant.id)


def test_writes_stop_once_the_grace_period_lapses(session, live_merchant):
    subscribe(
        session,
        live_merchant,
        "growth",
        status="past_due",
        grace_until=datetime.now(UTC) - timedelta(hours=1),
    )
    assert subscription_state(session, live_merchant.id).is_active is False
    with pytest.raises(BillingEntitlementError):
        assert_write_allowed(session, live_merchant.id)


def test_a_cancelled_subscription_says_so_rather_than_blaming_the_trial(session, live_merchant):
    subscribe(session, live_merchant, "growth", status="cancelled")
    state = subscription_state(session, live_merchant.id)

    assert state.is_active is False
    assert "cancelled" in (state.paused_reason or "").lower()


# ===========================================================================
# Tier features
# ===========================================================================


def test_starter_includes_zoho(session, live_merchant):
    """Zoho is how invoices get in, so every paying plan has it — Starter included."""
    subscribe(session, live_merchant, "starter")
    assert_feature_entitled(session, live_merchant.id, Feature.ZOHO_INTEGRATION)


def test_starter_cannot_use_growth_features(session, live_merchant):
    subscribe(session, live_merchant, "starter")
    with pytest.raises(BillingEntitlementError) as exc:
        assert_feature_entitled(session, live_merchant.id, Feature.CUSTOM_POLICIES)

    message = str(exc.value)
    assert "Starter" in message and "Growth" in message, (
        "the merchant must be told which plan actually unlocks it"
    )


def test_growth_unlocks_its_advertised_features(session, live_merchant):
    subscribe(session, live_merchant, "growth")
    for feature in (Feature.ZOHO_INTEGRATION, Feature.CUSTOM_POLICIES):
        assert_feature_entitled(session, live_merchant.id, feature)

    with pytest.raises(BillingEntitlementError):
        assert_feature_entitled(session, live_merchant.id, Feature.BILLING_RECONCILIATION)


def test_scale_unlocks_everything(session, live_merchant):
    subscribe(session, live_merchant, "scale")
    for feature in Feature:
        assert_feature_entitled(session, live_merchant.id, feature)


# ===========================================================================
# Seats
# ===========================================================================


def test_starter_seats_one_person(session, live_merchant, seat_holder):
    """Starter is a single-operator plan: the owner, and nobody else."""
    subscribe(session, live_merchant, "starter")
    assert subscription_state(session, live_merchant.id).plan.included_seats == 1
    with pytest.raises(BillingEntitlementError) as exc:
        assert_seat_entitled(session, live_merchant.id)
    assert "1 seat" in str(exc.value)


def test_growth_and_scale_seat_counts(session, live_merchant):
    assert GROWTH.included_seats == 5
    assert SCALE.included_seats == 15
    subscribe(session, live_merchant, "growth")
    assert_seat_entitled(session, live_merchant.id)  # 0 used of 5


# ===========================================================================
# Cancellation
# ===========================================================================


def test_cancelling_keeps_the_period_already_paid_for(session, live_merchant):
    subscribe(
        session,
        live_merchant,
        "growth",
        current_period_end=datetime.now(UTC) + timedelta(days=12),
    )
    state = cancel_subscription(session, live_merchant.id)
    session.commit()

    assert state.cancel_at_period_end is True
    assert state.is_active is True, "they paid for this month and keep it"
    assert state.days_remaining == 12


def test_cancelling_immediately_ends_access(session, live_merchant):
    subscribe(session, live_merchant, "growth")
    state = cancel_subscription(session, live_merchant.id, immediate=True)
    session.commit()

    assert state.status == "cancelled"
    assert state.is_active is False


def test_cancelling_without_a_subscription_is_refused(session, live_merchant):
    with pytest.raises(BillingEntitlementError):
        cancel_subscription(session, live_merchant.id)


def test_the_published_trial_is_seven_days(session, live_merchant):
    """The pricing page promises a 7-day Starter trial; the server must agree.

    Pinned because these are the same number in two places a customer can see.
    """
    assert settings.live_trial_days == 7

    from app.services.billing import start_trial

    start_trial(live_merchant)
    session.add(live_merchant)
    session.commit()
    subscribe(session, live_merchant, "starter", status="authenticated")

    state = subscription_state(session, live_merchant.id)
    assert state.on_trial is True
    assert state.days_remaining == 7
    assert state.plan.slug == STARTER.slug


def test_an_existing_trial_stamp_is_honoured_over_the_default(session, live_merchant):
    """A longer window already granted must not be silently shortened.

    Support extends trials by writing this field. Recomputing from `live_trial_days`
    would revoke an extension the moment the default changed.
    """
    set_trial(session, live_merchant, days_from_now=30)
    subscribe(session, live_merchant, "starter", status="authenticated")
    assert subscription_state(session, live_merchant.id).days_remaining == 30
