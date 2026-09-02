"""Run the background engine as a role that cannot bypass row-level security.

Every other test in this suite connects as the developer's own Postgres role, which is
almost always a superuser. Superusers bypass RLS unconditionally, so those tests cannot
observe tenant policies at all — which is how the entire background engine came to be
broken under the role production is supposed to use while 913 tests stayed green.

`scripts/create_app_role.sql` creates `vasooli_app` as NOSUPERUSER/NOBYPASSRLS for
exactly this reason. These tests build the equivalent role and drive the real code
paths through it:

* the recovery cycle must still see and act on invoices;
* the Razorpay webhook must still resolve a payment link to its invoice;
* an ordinary request context must still be confined to one tenant.

The last one is what stops the fix from becoming its own hole: `service_scope` grants a
cross-tenant *read* to background work, and this asserts that nothing about it leaks
into request-scoped access or lets a write land in the wrong tenant.
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.config import settings
from app.models import Customer, Invoice, Merchant, PaymentLink, Reminder
from app.services.authorization import merchant_scope, service_scope, set_merchant_context
from app.services.recovery import run_recovery_cycle

RESTRICTED_ROLE = "vasooli_rls_test"
RESTRICTED_PASSWORD = "rls-test-password"


@pytest.fixture(scope="module")
def restricted_engine(request):
    """An engine connected as a role that RLS actually applies to.

    Skips rather than fails where the test role cannot be created — a CI database whose
    user has no CREATEROLE right should not turn a green build red — but the skip is
    loud, because a suite that silently stops checking this is how the bug survived.
    """
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

    restricted_url = url.set(
        username=RESTRICTED_ROLE,
        password=RESTRICTED_PASSWORD,
        host=url.host or "127.0.0.1",
    ).render_as_string(hide_password=False)

    engine = create_engine(restricted_url)
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT current_setting('is_superuser')").scalar() == "off"
        yield engine
    finally:
        engine.dispose()
        owner.dispose()


def _two_tenants(session: Session) -> tuple[Invoice, Invoice]:
    """One overdue invoice in each of two live merchants."""
    invoices = []
    for name in ("Alpha Tenant", "Beta Tenant"):
        merchant = Merchant(
            name=name,
            legal_name=name,
            contact_email=f"{name.split()[0].lower()}-owner@example.invalid",
            mode="live",
            status="active",
            is_demo=False,
        )
        session.add(merchant)
        session.flush()
        set_merchant_context(session, merchant.id)
        customer = Customer(
            merchant_id=merchant.id,
            name=f"{name} Buyer",
            email=f"{name.split()[0].lower()}-buyer@example.invalid",
        )
        session.add(customer)
        session.flush()
        invoice = Invoice(
            merchant_id=merchant.id,
            customer_id=customer.id,
            invoice_number="INV-001",  # deliberately identical across tenants
            amount_paise=2_500_000,
            issued_at=utcnow().replace(year=utcnow().year - 1),
            due_at=utcnow().replace(year=utcnow().year - 1),
        )
        session.add(invoice)
        session.flush()
        invoices.append(invoice)
    session.commit()
    return invoices[0], invoices[1]


def test_recovery_cycle_sees_invoices_without_bypassing_rls(session, restricted_engine):
    """The cycle considered nothing at all before `service_scope` existed."""
    alpha, beta = _two_tenants(session)

    with Session(restricted_engine) as restricted:
        # Baseline: with no scope, the tenant policy hides everything from this role.
        assert restricted.exec(select(Invoice)).all() == []

        report = run_recovery_cycle(restricted, dry_run=True, use_llm=False)

    numbers = {alpha.invoice_number, beta.invoice_number}
    assert report.considered == 2, (
        "the recovery cycle must see both tenants' invoices under a NOBYPASSRLS role; "
        f"it considered {report.considered}"
    )
    assert report.errors == []
    assert numbers == {"INV-001"}


def test_webhook_can_resolve_a_payment_link_to_its_invoice(session, restricted_engine):
    """Routing happens before the tenant is known; without the scope, money is lost."""
    alpha, _ = _two_tenants(session)
    set_merchant_context(session, alpha.merchant_id)
    session.add(
        PaymentLink(
            invoice_id=alpha.id,
            razorpay_payment_link_id="plink_audit_1",
            reference_id="ref-audit-1",
            short_url="https://rzp.io/l/audit",
            amount_expected_paise=alpha.amount_paise,
        )
    )
    session.commit()

    with Session(restricted_engine) as restricted:
        # `payment_links` used to be readable with no scope at all, because it carries
        # no merchant_id and so the merchant-isolation sweep skipped it. It is now
        # isolated through its parent invoice, which is the whole point of the child
        # -table policies — so routing has to declare its cross-tenant read.
        assert (
            restricted.exec(
                select(PaymentLink).where(PaymentLink.razorpay_payment_link_id == "plink_audit_1")
            ).first()
            is None
        ), "payment_links must not be readable without a scope"
        assert restricted.get(Invoice, alpha.id) is None, (
            "baseline: the invoice behind the link is hidden without a scope"
        )

        with service_scope(restricted):
            link = restricted.exec(
                select(PaymentLink).where(PaymentLink.razorpay_payment_link_id == "plink_audit_1")
            ).first()
            assert link is not None, (
                "the webhook resolves a link to its invoice under service scope; if "
                "that read fails, a real payment is recorded as unmatched"
            )
            matched = restricted.get(Invoice, link.invoice_id)
        assert matched is not None, (
            "the Razorpay webhook must be able to reach the invoice a paid link belongs "
            "to, or a real payment is recorded as unmatched and the customer keeps "
            "being chased after paying"
        )
        assert matched.id == alpha.id


def test_service_scope_does_not_survive_the_block(session, restricted_engine):
    """A cross-tenant read must not outlive the work that needed it."""
    _two_tenants(session)

    with Session(restricted_engine) as restricted:
        with service_scope(restricted):
            assert len(restricted.exec(select(Invoice)).all()) == 2
        restricted.commit()
        assert restricted.exec(select(Invoice)).all() == [], (
            "service scope leaked past its block; a later request on this connection "
            "would see every tenant"
        )


def test_service_scope_survives_commits_inside_the_block(session, restricted_engine):
    """A transaction-local setting dies on commit; the cycle commits per invoice."""
    _two_tenants(session)

    with Session(restricted_engine) as restricted, service_scope(restricted):
        assert len(restricted.exec(select(Invoice)).all()) == 2
        restricted.commit()
        assert len(restricted.exec(select(Invoice)).all()) == 2, (
            "the scope stopped applying after the first commit, so the cycle would "
            "silently process only its first invoice"
        )


def test_service_scope_cannot_write_into_another_tenant(session, restricted_engine):
    """`WITH CHECK` is deliberately untouched: the grant is read-only."""
    alpha, beta = _two_tenants(session)

    with (
        Session(restricted_engine) as restricted,
        service_scope(restricted),
        merchant_scope(restricted, alpha.merchant_id),
    ):
        target = restricted.get(Invoice, beta.id)
        assert target is not None, "readable across tenants under service scope"
        target.escalation_reason = "cross-tenant write attempt"
        restricted.add(target)
        with pytest.raises(Exception, match="row-level security"):
            restricted.commit()
        restricted.rollback()


def test_merchant_scope_refuses_to_nest_a_different_tenant(session, restricted_engine):
    """Two merchants sharing one transaction is the confusion this all guards against."""
    alpha, beta = _two_tenants(session)

    with (
        Session(restricted_engine) as restricted,
        merchant_scope(restricted, alpha.merchant_id),
        pytest.raises(RuntimeError, match="already scoped"),
        merchant_scope(restricted, beta.merchant_id),
    ):
        pass


def test_a_request_context_still_sees_only_its_own_tenant(session, restricted_engine):
    """The fix must not widen ordinary authorized access."""
    alpha, beta = _two_tenants(session)

    with Session(restricted_engine) as restricted:
        set_merchant_context(restricted, alpha.merchant_id)
        visible = restricted.exec(select(Invoice)).all()
        assert [row.id for row in visible] == [alpha.id]
        assert restricted.get(Invoice, beta.id) is None
        assert restricted.get(Customer, beta.customer_id) is None


def test_every_tenant_table_carries_the_service_scope_clause(session):
    """A new tenant table without the clause reintroduces the same silent failure."""
    rows = session.exec(
        text(
            """
            SELECT c.relname, pg_get_expr(p.polqual, p.polrelid) AS using_clause
            FROM pg_policy p
            JOIN pg_class c ON c.oid = p.polrelid
            WHERE p.polname = c.relname || '_merchant_isolation'
            """
        )
    ).all()
    assert rows, "no merchant-isolation policies found; the migration did not run"
    missing = sorted(name for name, clause in rows if "app.service_role" not in (clause or ""))
    assert not missing, (
        "these tenant tables cannot be read by background jobs or webhook routing, so "
        f"work touching them will silently do nothing in production: {missing}"
    )


#: The eight tables that hang off `invoices` by `invoice_id` and carry no
#: `merchant_id`. They were skipped by the merchant-isolation sweep because that
#: matched on a column they do not have.
INVOICE_CHILD_TABLES = (
    "audit_logs",
    "dispute_cases",
    "email_events",
    "external_payments",
    "inbound_messages",
    "payment_links",
    "promises",
    "reminders",
)


def test_every_invoice_child_table_is_isolated(session):
    """A child table without a policy is an IDOR waiting for a careless endpoint.

    These carry no `merchant_id`, so `_merchant_isolation` never covered them and the
    gap was invisible to the check above. Ownership comes from the parent invoice
    instead, which is self-scoping because `invoices` is itself under forced RLS.
    """
    rows = session.exec(
        text(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   pg_get_expr(p.polqual, p.polrelid) AS using_clause
            FROM pg_class c
            LEFT JOIN pg_policy p
              ON p.polrelid = c.oid AND p.polname = c.relname || '_invoice_isolation'
            WHERE c.relname = ANY(:names)
            """
        ).bindparams(names=list(INVOICE_CHILD_TABLES))
    ).all()
    found = {name: (enabled, forced, clause) for name, enabled, forced, clause in rows}
    assert set(found) == set(INVOICE_CHILD_TABLES), "a child table is missing from the schema"

    for table, (enabled, forced, clause) in sorted(found.items()):
        assert enabled and forced, f"{table} does not force row-level security"
        assert clause, f"{table} has no {table}_invoice_isolation policy"
        # Without this, background work reads nothing and silently does nothing —
        # the same failure mode the merchant-isolation sweep exists to prevent.
        assert "app.service_role" in clause, f"{table} is unreadable under service scope"


def test_a_child_row_is_hidden_from_another_tenant(session, restricted_engine):
    """The backstop itself: the parent decides who may see the child."""
    alpha, beta = _two_tenants(session)
    set_merchant_context(session, alpha.merchant_id)
    session.add(
        Reminder(
            invoice_id=alpha.id,
            tier=1,
            tone="firm",
            subject="s",
            body="b",
            channel="email",
            policy_decision={},
        )
    )
    session.commit()

    with Session(restricted_engine) as restricted:
        set_merchant_context(restricted, alpha.merchant_id)
        assert [r.invoice_id for r in restricted.exec(select(Reminder)).all()] == [alpha.id]

    with Session(restricted_engine) as restricted:
        # Beta owns no reminders, and must not be able to read Alpha's.
        set_merchant_context(restricted, beta.merchant_id)
        assert restricted.exec(select(Reminder)).all() == []


def test_service_scope_cannot_write_a_child_row_into_another_tenant(session, restricted_engine):
    """Service scope reads across tenants; it must still write into none of them.

    `WITH CHECK` deliberately does not reuse the `USING` subquery, which widens to
    every invoice under service scope. Sharing one expression would have let
    background work attach a reminder to somebody else's invoice.
    """
    _alpha, beta = _two_tenants(session)

    with Session(restricted_engine) as restricted, service_scope(restricted):
        restricted.add(
            Reminder(
                invoice_id=beta.id,
                tier=2,
                tone="firm",
                subject="s",
                body="b",
                channel="email",
                policy_decision={},
            )
        )
        with pytest.raises(ProgrammingError, match="row-level security"):
            restricted.flush()
        # `service_scope` clears its setting on the way out, which needs a usable
        # session; without this the cleanup raises and masks the refusal above.
        restricted.rollback()
