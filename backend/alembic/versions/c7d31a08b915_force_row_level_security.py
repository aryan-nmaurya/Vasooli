"""force row level security on tenant tables

Revision ID: c7d31a08b915
Revises: f42b7d1c9e53

`ENABLE ROW LEVEL SECURITY` alone does nothing for the role that owns the table —
Postgres exempts owners unless `FORCE` is also set. The tenancy migration documented
that as deliberate, on the stated precondition that "deployed application roles must
be non-owners". That precondition is not met: the deployed app connects as the role
that ran the migrations, so it owns every table and every policy was inert.

Proven before this change, scoped to a merchant owning nothing:

    SET LOCAL app.merchant_id = '1111...';
    SELECT count(*) FROM invoices;   -- 15, should be 0

Forcing it makes the policies apply to the owner too. Superusers still bypass RLS
unconditionally — no table setting can change that — so the deployed role must also
be non-superuser. `scripts/preflight.py` now checks both rather than assuming them.
"""

from alembic import op

revision = "c7d31a08b915"
down_revision = "f42b7d1c9e53"
branch_labels = None
depends_on = None

TABLES = (
    "customers",
    "invoices",
    "roles",
    "merchant_invitations",
    "merchant_memberships",
    "user_permission_overrides",
    "audit_events",
    "billing_customers",
    "billing_subscriptions",
    "billing_entitlements",
    "billing_invoices",
    "billing_payment_attempts",
    "billing_refunds",
    "razorpay_connections",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
