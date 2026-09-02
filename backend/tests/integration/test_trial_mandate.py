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
        self.refund_id = refund_id

    def create_subscription(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "sub_test", "short_url": "https://rzp.io/s/x"}

    def refund_payment(self, payment_id, *, amount_paise=None, notes=None):
        self.refunded.append((payment_id, amount_paise))
        return {"id": self.refund_id, "status": "processed"}


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
        status="authenticated",
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
