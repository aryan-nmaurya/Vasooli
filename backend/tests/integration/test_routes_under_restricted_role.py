"""Request handlers, run as the role production actually connects as.

Every other test in this suite connects as the developer's Postgres superuser, which
bypasses row-level security entirely. That blindness has now produced the same live
500 three times, in three different files, and `test_rls_under_restricted_role.py`
could not catch any of them because it exercises services rather than routes.

The shape of the bug:

    set_merchant_context(session, merchant.id)   # transaction-local
    ...
    session.commit()                             # <- the setting dies here
    return {"id": str(row.id)}                   # <- re-SELECT, no tenant, 0 rows

`expire_on_commit` is True (`app/core/db.get_session` uses a plain `Session`), so any
attribute touched after the commit re-reads from the database. Under a NOBYPASSRLS
role the policy then matches nothing and SQLAlchemy raises `ObjectDeletedError`, which
reaches the merchant as a bare 500.

Each test below calls the handler exactly as FastAPI would and asserts it returns a
response. Delete the `merchant_scope` from any of the handlers and the matching test
fails with ObjectDeletedError — that is the whole point of the file.
"""

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlmodel import Session

from app.core.config import settings
from app.models import BillingSubscription, Customer, Invoice, Merchant, User
from app.services.authorization import LiveContext, set_merchant_context

RESTRICTED_ROLE = "vasooli_routes_test"
RESTRICTED_PASSWORD = "routes-test-password"


@pytest.fixture(scope="module")
def restricted_engine(request):
    """An engine whose role the RLS policies actually apply to."""
    request.getfixturevalue("migrated_database")
    url = make_url(settings.database_url)
    owner = create_engine(settings.database_url, poolclass=None)
    try:
        with owner.begin() as conn:
            conn.exec_driver_sql(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RESTRICTED_ROLE}') THEN
                        CREATE ROLE {RESTRICTED_ROLE} LOGIN;
                    END IF;
                END $$;
                """
            )
            conn.exec_driver_sql(
                f"ALTER ROLE {RESTRICTED_ROLE} WITH PASSWORD '{RESTRICTED_PASSWORD}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
            conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {RESTRICTED_ROLE}")
            conn.exec_driver_sql(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
                f"TO {RESTRICTED_ROLE}"
            )
            conn.exec_driver_sql(
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {RESTRICTED_ROLE}"
            )
    except Exception as exc:  # noqa: BLE001 - environment capability, not a defect
        pytest.skip(f"cannot create a NOBYPASSRLS role here: {exc}")

    engine = create_engine(
        url.set(
            username=RESTRICTED_ROLE,
            password=RESTRICTED_PASSWORD,
            host=url.host or "127.0.0.1",
        ).render_as_string(hide_password=False)
    )
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT current_setting('is_superuser')").scalar() == "off"
        yield engine
    finally:
        engine.dispose()
        owner.dispose()


@pytest.fixture
def merchant(session) -> Merchant:
    """A live merchant, created as the owner so the restricted role can then act on it."""
    row = Merchant(
        name="Restricted Route Ltd",
        legal_name="Restricted Route Ltd",
        contact_email=f"owner-{uuid.uuid4().hex[:8]}@example.invalid",
        mode="live",
        status="active",
        is_demo=False,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.fixture
def paid_merchant(session, merchant) -> Merchant:
    """A merchant whose subscription is live.

    `assert_write_allowed` refuses billable writes without one, which is correct and is
    the payment gate doing its job — but it means a bare merchant cannot reach the
    handler body these tests exist to exercise.
    """
    from app.services.billing import ensure_plans

    plan = next(p for p in ensure_plans(session) if p.slug == "starter")
    session.add(BillingSubscription(merchant_id=merchant.id, plan_id=plan.id, status="active"))
    session.commit()
    return merchant


@pytest.fixture
def actor(session, merchant) -> User:
    """A real user row: handlers write `context.user.id` into the audit trail."""
    row = User(
        email=f"actor-{uuid.uuid4().hex[:8]}@example.invalid",
        display_name="Restricted Actor",
        password_hash="x" * 32,
        status="active",
        is_email_verified=True,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.fixture
def restricted_session(restricted_engine, merchant):
    """A session on the restricted role, with the tenant pinned as a request would."""
    with Session(restricted_engine) as s:
        set_merchant_context(s, merchant.id)
        yield s


def _context(merchant: Merchant, permission: str, user: User | None = None) -> LiveContext:
    """The guard already satisfied, so the handler body is what gets exercised."""
    return LiveContext(
        user=user,
        merchant=merchant,
        membership=None,
        session=None,
        permission=permission,
    )


class _Request:
    """Enough of a Request for `client_ip`."""

    def __init__(self) -> None:
        self.client = None
        self.headers: dict[str, str] = {}


def test_add_suppression_survives_its_own_commit(restricted_session, merchant):
    from app.api import controls

    body = controls.add_suppression(
        payload=controls.SuppressionRequest(email="AP@example.invalid", reason="hard_bounce"),
        session=restricted_session,
        context=_context(merchant, "policy.manage"),
    )
    assert body["status"] == "active"
    assert body["reason"] == "hard_bounce"
    assert uuid.UUID(body["id"])


def test_create_data_request_survives_its_own_commit(restricted_session, merchant, actor):
    from app.api import operations

    body = operations.request_export(
        payload=operations.DataRequestPayload(reason="audit evidence for the review"),
        request=_Request(),
        session=restricted_session,
        context=_context(merchant, "merchant.manage", user=actor),
    )
    assert body["type"] == "export"
    assert uuid.UUID(body["id"])


def test_put_policy_survives_its_own_commit(restricted_session, paid_merchant, actor):
    from app.api import controls

    body = controls.put_policy(
        payload=controls.PolicyRequest(preset="3_7_14"),
        request=_Request(),
        session=restricted_session,
        context=_context(paid_merchant, "reminder.configure", user=actor),
    )
    # The full dict is built from the row AFTER the commit; every field here is a
    # separate attribute read that would re-SELECT without the scope.
    assert body["tier_offsets"]
    assert body["version"] >= 1


def test_the_tenant_setting_really_does_die_at_commit(restricted_session, merchant):
    """The mechanism itself, so the tests above cannot pass for the wrong reason.

    If this ever starts failing, `set_config(..., true)` has stopped being
    transaction-local and the `merchant_scope` wrappers are load-bearing for a
    different reason than the one documented.
    """
    before = restricted_session.exec(
        text("SELECT current_setting('app.merchant_id', true)")
    ).scalar()
    assert before == str(merchant.id)

    restricted_session.commit()

    after = restricted_session.exec(
        text("SELECT current_setting('app.merchant_id', true)")
    ).scalar()
    assert after in (None, ""), (
        "the transaction-local tenant survived a commit; the post-commit re-SELECT "
        "hazard these tests guard against may have changed shape"
    )


def test_an_inbound_reply_is_processed_under_the_production_role(
    session, restricted_engine, merchant
):
    """The customer-reply path, which failed silently rather than loudly.

    `_record_inbound` commits the stored message and then calls `handle_reply`, which
    re-reads the invoice. Without a held tenant that read matched nothing under this
    role — and the `except Exception` around it marked the message for retry and
    answered the provider 200. So the reply was never processed, the retry hit the
    same fault, and nothing surfaced except a growing exceptions queue.
    """
    from app.core.clock import utcnow
    from app.services.authorization import merchant_scope
    from app.services.replies import handle_reply

    # Seeded as the owner; acted on as the restricted role below.
    customer = Customer(merchant_id=merchant.id, name="Reply Co", email="ap@reply.example.invalid")
    session.add(customer)
    session.flush()
    invoice = Invoice(
        merchant_id=merchant.id,
        customer_id=customer.id,
        invoice_number=f"INV-R{uuid.uuid4().hex[:6]}",
        amount_paise=2_500_000,
        issued_at=utcnow(),
        due_at=utcnow(),
    )
    session.add(invoice)
    session.commit()
    invoice_id = invoice.id

    with Session(restricted_engine) as restricted, merchant_scope(restricted, merchant.id):
        live_invoice = restricted.get(Invoice, invoice_id)
        assert live_invoice is not None, "the tenant scope should expose its own invoice"
        # A commit mid-flow is what killed the tenant before; the scope must survive it.
        restricted.commit()
        outcome = handle_reply(restricted, live_invoice, "We will pay on the 15th.", use_llm=False)

    assert outcome.invoice_number == live_invoice.invoice_number


def test_the_razorpay_sweep_can_record_what_it_finds(session, restricted_engine, merchant):
    """The safety net for a missed webhook, which could never write anything down.

    `sync_payment_links` reads across tenants under `service_scope` — correct, since
    it has to find links before it knows whose they are — and then INSERTED a
    reconciliation event and an audit row while still in that scope. The grant is
    read-only by construction: every policy's WITH CHECK demands a real
    `app.merchant_id`, so Postgres refused the write with "new row violates row-level
    security policy", and the failed flush poisoned the session so the first
    unreconciled link killed every link after it in the same sweep.

    A payment made while a webhook was undeliverable therefore stayed unrecorded
    forever, which is the exact case this job exists to cover.
    """
    from app.core.clock import utcnow
    from app.models import PaymentLink
    from app.services.authorization import service_scope
    from app.services.sync import sync_payment_links

    customer = Customer(merchant_id=merchant.id, name="Sweep Co", email="ap@sweep.example.invalid")
    session.add(customer)
    session.flush()
    invoice = Invoice(
        merchant_id=merchant.id,
        customer_id=customer.id,
        invoice_number=f"INV-S{uuid.uuid4().hex[:6]}",
        amount_paise=3_340_000,
        issued_at=utcnow(),
        due_at=utcnow(),
    )
    session.add(invoice)
    session.flush()
    link_id = f"plink_{uuid.uuid4().hex[:12]}"
    session.add(
        PaymentLink(
            invoice_id=invoice.id,
            razorpay_payment_link_id=link_id,
            reference_id=f"vsl-{invoice.invoice_number}",
            short_url="https://rzp.io/rzp/sweeptest",
            status="created",
            amount_expected_paise=invoice.amount_paise,
            amount_paid_paise=0,
            accept_partial=True,
            raw_response={},
        )
    )
    session.commit()
    invoice_id = invoice.id
    paid = invoice.amount_paise

    class _PaidAtRazorpay:
        """Razorpay reporting the link as paid, which is what the sweep is for."""

        status = "paid"
        amount_paid_paise = paid
        # The real link id: reconciliation matches on it, so a placeholder here would
        # make the test pass the write and fail the match for an unrelated reason.
        raw = {"id": link_id, "status": "paid", "amount_paid": paid}

        def fetch_payment_link(self, _link_id):
            return self

    # Exactly how the job runs it: cross-tenant read scope, restricted role.
    with Session(restricted_engine) as restricted, service_scope(restricted):
        report = sync_payment_links(restricted, client=_PaidAtRazorpay())

    assert report["errors"] == 0, "the sweep could not write down what it found"
    assert report["recovered"] == 1

    session.expire_all()
    assert session.get(Invoice, invoice_id).is_fully_paid
