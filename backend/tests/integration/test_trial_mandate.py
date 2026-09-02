"""Starting a trial authorises an Autopay mandate with a small, refunded charge.

A mandate cannot be validated for nothing: the bank or UPI app confirms the customer
approved recurring debits by taking a payment. That amount is charged at
authorisation and returned once the subscription reports itself authenticated, so the
trial stays free in substance while the mandate is real — and the first post-trial
charge runs against an instrument already proven to work.
"""

from datetime import UTC, datetime

import pytest
from sqlmodel import select

from app.core.config import settings
from app.models import BillingRefund, BillingSubscription, Merchant
from app.services import billing as billing_mod
from app.services.billing import (
    create_provider_subscription,
    ensure_plans,
    refund_mandate_verification,
)


@pytest.fixture
def live_merchant(session):
    m = Merchant(name="Trial Ltd", contact_email="ops@trial.example", is_demo=False, mode="live")
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


@pytest.fixture
def plan(session):
    plans = ensure_plans(session)
    for p in plans:
        p.razorpay_plan_id = f"plan_{p.slug}"
    session.commit()
    return next(p for p in plans if p.slug == "starter")


class FakeClient:
    def __init__(self, refund_id="rfnd_1"):
        self.created: list[dict] = []
        self.refunded: list[tuple] = []
        self.cancelled: list[tuple] = []
        self.refund_id = refund_id

    def create_subscription(self, **kwargs):
        self.created.append(kwargs)
        # Unique per call, as Razorpay's are: `razorpay_subscription_id` is uniquely
        # indexed, so a constant id makes a second checkout collide in the fake only.
        return {"id": f"sub_test_{len(self.created)}", "short_url": "https://rzp.io/s/x"}

    def refund_payment(self, payment_id, *, amount_paise=None, notes=None):
        self.refunded.append((payment_id, amount_paise))
        return {"id": self.refund_id, "status": "processed"}

    def cancel_subscription(self, subscription_id, *, cancel_at_cycle_end=True):
        self.cancelled.append((subscription_id, cancel_at_cycle_end))
        return {"id": subscription_id, "status": "cancelled"}


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(settings, "razorpay_subscriptions_enabled", True, raising=False)
    monkeypatch.setattr(billing_mod, "get_billing_client", lambda: client)
    return client


# ===========================================================================
# Creating the subscription
# ===========================================================================


def test_a_trial_delays_billing_and_charges_only_the_verification_amount(plan, fake):
    create_provider_subscription(plan, trial_days=7)

    sent = fake.created[0]
    assert sent["start_at"] is not None, "the plan amount must not be charged during the trial"
    started = datetime.fromtimestamp(sent["start_at"], UTC)
    assert 6 <= (started - datetime.now(UTC)).days <= 7
    assert sent["auth_amount_paise"] == settings.trial_auth_amount_paise


def test_the_verification_amount_is_two_rupees():
    """Published as ₹2. It is refunded, but the merchant still sees it leave."""
    assert settings.trial_auth_amount_paise == 200


def test_an_upgrade_is_billed_immediately_with_no_verification_charge(plan, fake):
    """Only a first-time trial needs the mandate proving; an existing payer has one."""
    create_provider_subscription(plan, trial_days=None)

    sent = fake.created[0]
    assert sent["start_at"] is None
    assert sent["auth_amount_paise"] is None


# ===========================================================================
# Refunding it
# ===========================================================================


def _subscription(session, merchant, plan, **kw):
    row = BillingSubscription(
        merchant_id=merchant.id,
        plan_id=plan.id,
        status=kw.pop("status", "authenticated"),
        auth_payment_id="pay_1",
        auth_amount_paise=200,
        **kw,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_the_verification_charge_is_refunded_once_authenticated(session, live_merchant, plan, fake):
    row = _subscription(session, live_merchant, plan)

    refund_id = refund_mandate_verification(session, row)
    session.commit()

    assert refund_id == "rfnd_1"
    assert fake.refunded == [("pay_1", 200)]
    assert row.auth_refund_id == "rfnd_1"
    stored = session.exec(select(BillingRefund)).all()
    assert len(stored) == 1
    assert stored[0].amount_paise == 200


def test_a_redelivered_webhook_does_not_refund_twice(session, live_merchant, plan, fake):
    """Razorpay redelivers webhooks, and a second refund returns real money twice."""
    row = _subscription(session, live_merchant, plan)

    first = refund_mandate_verification(session, row)
    session.commit()
    second = refund_mandate_verification(session, row)
    session.commit()

    assert first == second
    assert len(fake.refunded) == 1, "the provider must be called exactly once"
    assert len(session.exec(select(BillingRefund)).all()) == 1


def test_a_provider_failure_leaves_it_to_be_retried(session, live_merchant, plan, monkeypatch):
    """The merchant is validly authenticated either way; do not wedge their billing."""

    class Failing(FakeClient):
        def refund_payment(self, *a, **k):
            raise RuntimeError("razorpay unavailable")

    monkeypatch.setattr(settings, "razorpay_subscriptions_enabled", True, raising=False)
    monkeypatch.setattr(billing_mod, "get_billing_client", lambda: Failing())
    row = _subscription(session, live_merchant, plan)

    assert refund_mandate_verification(session, row) is None
    assert row.auth_refund_id is None, "left unset so the next delivery retries it"


def test_nothing_is_refunded_when_no_verification_was_taken(session, live_merchant, plan, fake):
    row = BillingSubscription(
        merchant_id=live_merchant.id, plan_id=plan.id, status="active", auth_amount_paise=0
    )
    session.add(row)
    session.commit()

    assert refund_mandate_verification(session, row) is None
    assert fake.refunded == []


@pytest.fixture
def plan_ids_match_config(monkeypatch):
    """Keep configured plan ids in step with what the `plan` fixture writes.

    `checkout` calls `ensure_plans`, which refuses to re-point an immutable plan — so
    without this the endpoint answers 503 before reaching the decision under test.
    """
    for slug in ("starter", "growth", "scale"):
        monkeypatch.setattr(settings, f"razorpay_plan_id_{slug}", f"plan_{slug}", raising=False)


def _context(session, merchant):
    """A LiveContext for calling the endpoint function directly.

    The checkout route is guarded by `require_live_reauth`, which needs a session, a
    membership and a burnt challenge. Building all of that here would test FastAPI's
    dependency wiring rather than the billing decision, which is where both defects
    were — so the guard is supplied already-satisfied and the decision is exercised
    for real.
    """
    from app.services.authorization import LiveContext, set_merchant_context

    set_merchant_context(session, merchant.id)
    return LiveContext(
        user=None,
        merchant=merchant,
        membership=None,
        session=None,
        permission="billing.manage",
    )


# --- What checkout actually decides -------------------------------------------------
#
# Everything above proves the mandate works once `create_provider_subscription` is
# given `trial_days`. Nothing proved the endpoint ever gives it, and no test in the
# suite called `/api/live/billing/checkout` at all — so the decision shipped inverted:
# the trial was offered on the condition `subscription_state(...).on_trial`, which only
# becomes true after the mandate is authenticated, and is therefore false for exactly
# the merchant signing up. They were sent to authorise the full plan amount.


def test_a_merchant_who_has_never_subscribed_is_offered_the_trial(session, live_merchant, fake):
    assert billing_mod.trial_is_available(session, live_merchant.id) is True
    assert billing_mod.mandate_verification_paise(session, live_merchant.id) == 200


def test_a_returning_merchant_does_not_collect_a_second_trial(session, live_merchant, plan, fake):
    row = _subscription(session, live_merchant, plan, status="cancelled")
    assert row.status == "cancelled"
    # Cancelled is a dead state, so `subscription_state` reports no live subscription —
    # but the merchant is returning, not new, and a fresh trial on every resubscribe
    # would be a free tier with extra steps.
    assert billing_mod.trial_is_available(session, live_merchant.id) is False
    assert billing_mod.mandate_verification_paise(session, live_merchant.id) is None


def test_signup_checkout_asks_for_the_trial_and_the_verification_charge(
    session, live_merchant, plan, fake, plan_ids_match_config
):
    """The regression itself, at the endpoint that had no coverage."""
    from app.api import billing as billing_api

    context = _context(session, live_merchant)
    body = billing_api.checkout(billing_api.CheckoutRequest(plan_slug="starter"), session, context)

    assert body["checkout_required"] is True
    sent = fake.created[-1]
    assert sent["auth_amount_paise"] == 200, (
        "a signing-up merchant must authorise ₹2, not the full plan amount"
    )
    assert sent["start_at"] is not None, "the first billing cycle must be pushed past the trial"


def test_an_abandoned_checkout_does_not_trap_the_merchant_on_that_plan(
    session, live_merchant, plan, fake, plan_ids_match_config
):
    """Picking a different plan after walking away from a checkout must work.

    A `created` row was treated as an active subscription, so a second choice returned
    409 "cancel it before changing plans" — while the billing page only offers Cancel
    for an ACTIVE subscription. There was nothing to cancel and no way forward.
    """
    from app.api import billing as billing_api

    context = _context(session, live_merchant)
    billing_api.checkout(billing_api.CheckoutRequest(plan_slug="starter"), session, context)

    body = billing_api.checkout(billing_api.CheckoutRequest(plan_slug="growth"), session, context)
    assert body["plan"] == "growth"

    rows = session.exec(
        select(BillingSubscription).where(BillingSubscription.merchant_id == live_merchant.id)
    ).all()
    starter_rows = [r for r in rows if r.status == "cancelled"]
    assert starter_rows, "the abandoned checkout should be retired, not left in `created`"


def test_a_live_subscription_still_requires_an_explicit_cancellation(
    session, live_merchant, plan, fake, plan_ids_match_config
):
    """The dead-end fix must not let anyone silently replace a paid subscription."""
    import fastapi

    from app.api import billing as billing_api

    _subscription(session, live_merchant, plan, status="active")
    context = _context(session, live_merchant)
    with pytest.raises(fastapi.HTTPException) as caught:
        billing_api.checkout(billing_api.CheckoutRequest(plan_slug="growth"), session, context)
    assert caught.value.status_code == 409
