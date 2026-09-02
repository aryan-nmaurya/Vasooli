"""Payment links must live on the merchant's own Razorpay account.

The account a link is created on is the account it can be read from. A link issued on
a merchant's Razorpay and later fetched with platform credentials does not come back
as "unpaid" — it comes back as a permanent error, which reconciliation counts as a
failure and moves past. The customer's money has arrived, the invoice still says
overdue, and the merchant keeps chasing someone who already paid.

These pin that every path which creates, fetches or cancels a link resolves the
credentials per merchant.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models import Customer, Invoice, Merchant, PaymentConnection
from app.services.payment_connections import (
    PaymentConnectionRequiredError,
    encrypt_secret,
    razorpay_client_for_merchant,
)


@pytest.fixture
def live_merchant(session):
    m = Merchant(
        name="Routed Ltd",
        contact_email="ops@routed.example",
        is_demo=False,
        mode="live",
    )
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


@pytest.fixture
def demo_merchant(session):
    m = Merchant(name="Demo Co", contact_email="demo@example.invalid", is_demo=True, mode="demo")
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


def connect(session, merchant, *, key_id="rzp_test_MERCHANTPLACEHOLDER"):
    row = PaymentConnection(
        merchant_id=merchant.id,
        mode="byok",
        provider_account_id="acc_merchant",
        api_key_id=key_id,
        api_key_secret_encrypted=encrypt_secret("merchant-secret"),
        status="connected",
    )
    session.add(row)
    session.commit()
    return row


def make_invoice(session, merchant, customer, *, overdue=5, number="INV-ROUTE"):
    due = datetime.now(UTC) - timedelta(days=overdue)
    inv = Invoice(
        merchant_id=merchant.id,
        customer_id=customer.id,
        invoice_number=number,
        amount_paise=500_000,
        issued_at=due - timedelta(days=30),
        due_at=due,
    )
    session.add(inv)
    session.commit()
    session.refresh(inv)
    return inv


# ===========================================================================
# The resolver
# ===========================================================================


def test_a_connected_merchant_resolves_to_their_own_credentials(session, live_merchant):
    connect(session, live_merchant)
    client = razorpay_client_for_merchant(session, live_merchant.id)
    assert client._api_key_id == "rzp_test_MERCHANTPLACEHOLDER"


def test_a_live_merchant_without_a_connection_is_refused(session, live_merchant):
    """Fail closed. Falling back to the platform account would bill the customer into
    Vasooli's own Razorpay rather than the merchant's."""
    with pytest.raises(PaymentConnectionRequiredError) as exc:
        razorpay_client_for_merchant(session, live_merchant.id)
    assert "Connect a Razorpay collection account" in str(exc.value)


def test_a_revoked_connection_is_not_used(session, live_merchant):
    row = connect(session, live_merchant)
    row.revoked_at = datetime.now(UTC)
    session.add(row)
    session.commit()

    with pytest.raises(PaymentConnectionRequiredError):
        razorpay_client_for_merchant(session, live_merchant.id)


def test_the_demo_merchant_uses_the_platform_account(session, demo_merchant):
    """The demo has no merchant credentials to connect, and its Razorpay is test mode."""
    client = razorpay_client_for_merchant(session, demo_merchant.id)
    assert client is not None


def test_an_unknown_merchant_is_refused(session):
    with pytest.raises(PaymentConnectionRequiredError):
        razorpay_client_for_merchant(session, uuid.uuid4())


# ===========================================================================
# Provisioning routes through the resolver
# ===========================================================================


def test_provisioning_refuses_a_live_merchant_with_no_connection(session, live_merchant):
    from app.services.provisioning import provision_for_invoice

    customer = Customer(merchant_id=live_merchant.id, name="Buyer", email="b@example.invalid")
    session.add(customer)
    session.commit()
    session.refresh(customer)
    invoice = make_invoice(session, live_merchant, customer)

    with pytest.raises(PaymentConnectionRequiredError):
        provision_for_invoice(session, invoice.id)


def test_provisioning_uses_the_merchants_client_when_connected(session, live_merchant, monkeypatch):
    from app.services import provisioning as provisioning_mod

    seen: list[str] = []

    class FakeClient:
        def create_payment_link(self, **kwargs):
            seen.append("merchant")
            from app.integrations.razorpay_client import PaymentLinkResult

            return PaymentLinkResult.from_payload(
                {
                    "id": "plink_merchant",
                    "short_url": "https://rzp.io/i/merchant",
                    "status": "created",
                    "amount_paid": 0,
                }
            )

    connect(session, live_merchant)
    monkeypatch.setattr(
        provisioning_mod, "razorpay_client_for_merchant", lambda *a, **k: FakeClient()
    )

    customer = Customer(merchant_id=live_merchant.id, name="Buyer", email="b@example.invalid")
    session.add(customer)
    session.commit()
    session.refresh(customer)
    invoice = make_invoice(session, live_merchant, customer, number="INV-ROUTE-2")

    link = provisioning_mod.provision_for_invoice(session, invoice.id)
    assert seen == ["merchant"], "the link must be created on the merchant's account"
    assert link.razorpay_payment_link_id == "plink_merchant"


# ===========================================================================
# Reconciliation routes through the resolver
# ===========================================================================


def test_sync_skips_a_merchant_whose_connection_is_gone(session, live_merchant):
    """A disconnected merchant is reported, not retried against the wrong account."""
    from app.models import PaymentLink
    from app.services import sync as sync_mod

    customer = Customer(merchant_id=live_merchant.id, name="Buyer", email="b@example.invalid")
    session.add(customer)
    session.commit()
    session.refresh(customer)
    invoice = make_invoice(session, live_merchant, customer, number="INV-ROUTE-3")
    session.add(
        PaymentLink(
            invoice_id=invoice.id,
            razorpay_payment_link_id="plink_orphan",
            reference_id=f"vsl-{invoice.id.hex}",
            short_url="https://rzp.io/i/orphan",
            status="created",
            amount_expected_paise=invoice.amount_paise,
        )
    )
    session.commit()

    # Structural, not mocked: the platform-client factory is no longer reachable from
    # this module at all, so there is nothing left to accidentally fall back to.
    assert not hasattr(sync_mod, "get_razorpay_client")

    result = sync_mod.sync_payment_links(session, invoice_number="INV-ROUTE-3")

    assert result["checked"] == 1
    assert result["errors"] == 1
    assert result["recovered"] == 0
