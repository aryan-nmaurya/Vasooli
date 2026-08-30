"""add subscription billing, entitlements and per-merchant Razorpay connections

Revision ID: f42b7d1c9e53
Revises: e31f6a9c7d42
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f42b7d1c9e53"
down_revision: str | Sequence[str] | None = "e31f6a9c7d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id(name: str) -> sa.Column:
    return sa.Column(name, sa.UUID(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "billing_plans",
        _id("id"),
        sa.Column("slug", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("razorpay_plan_id", sa.String(120)),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("included_active_invoices", sa.Integer(), nullable=False),
        sa.Column("included_seats", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", "version", name="uq_billing_plans_slug_version"),
    )
    op.create_index("ix_billing_plans_slug", "billing_plans", ["slug"])
    op.create_index(
        "ix_billing_plans_razorpay_plan_id", "billing_plans", ["razorpay_plan_id"], unique=True
    )

    op.create_table(
        "billing_customers",
        _id("id"),
        sa.Column("merchant_id", sa.UUID(), nullable=False),
        sa.Column("provider_customer_id", sa.String(120), nullable=False),
        sa.Column("billing_email", sa.String(320), nullable=False),
        sa.Column("legal_name", sa.String(240)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id"),
        sa.UniqueConstraint("provider_customer_id"),
    )
    op.create_index(
        "ix_billing_customers_merchant_id", "billing_customers", ["merchant_id"], unique=True
    )

    op.create_table(
        "billing_subscriptions",
        _id("id"),
        sa.Column("merchant_id", sa.UUID(), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("razorpay_subscription_id", sa.String(120)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True)),
        sa.Column("current_period_end", sa.DateTime(timezone=True)),
        sa.Column("grace_until", sa.DateTime(timezone=True)),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('created','authenticated','active','past_due','paused','cancelled','expired')",
            name="ck_billing_subscription_status",
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["billing_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_billing_subscriptions_merchant_id", "billing_subscriptions", ["merchant_id"]
    )
    op.create_index("ix_billing_subscriptions_plan_id", "billing_subscriptions", ["plan_id"])
    op.create_index(
        "ix_billing_subscriptions_razorpay_subscription_id",
        "billing_subscriptions",
        ["razorpay_subscription_id"],
        unique=True,
    )
    op.create_index("ix_billing_subscriptions_status", "billing_subscriptions", ["status"])

    op.create_table(
        "billing_events",
        _id("id"),
        sa.Column("provider_event_id", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("signature_verified", sa.Boolean(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(80)),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_billing_events_provider_event_id", "billing_events", ["provider_event_id"], unique=True
    )
    op.create_index("ix_billing_events_event_type", "billing_events", ["event_type"])

    op.create_table(
        "billing_entitlements",
        _id("id"),
        sa.Column("merchant_id", sa.UUID(), nullable=False),
        sa.Column("feature", sa.String(80), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "feature", name="uq_billing_entitlement_feature"),
    )
    op.create_index("ix_billing_entitlements_merchant_id", "billing_entitlements", ["merchant_id"])

    for table, columns in {
        "billing_invoices": [
            ("id", sa.UUID(), False),
            ("merchant_id", sa.UUID(), False),
            ("subscription_id", sa.UUID(), True),
            ("provider_invoice_id", sa.String(120), True),
            ("amount_paise", sa.BigInteger(), False),
            ("status", sa.String(30), False),
            ("issued_at", sa.DateTime(timezone=True), False),
        ],
        "billing_payment_attempts": [
            ("id", sa.UUID(), False),
            ("merchant_id", sa.UUID(), False),
            ("subscription_id", sa.UUID(), True),
            ("provider_payment_id", sa.String(120), True),
            ("amount_paise", sa.BigInteger(), False),
            ("status", sa.String(30), False),
            ("created_at", sa.DateTime(timezone=True), False),
        ],
        "billing_refunds": [
            ("id", sa.UUID(), False),
            ("merchant_id", sa.UUID(), False),
            ("provider_refund_id", sa.String(120), False),
            ("amount_paise", sa.BigInteger(), False),
            ("status", sa.String(30), False),
            ("created_at", sa.DateTime(timezone=True), False),
        ],
    }.items():
        cols = [sa.Column(name, typ, nullable=not optional) for name, typ, optional in columns]
        fks = [sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"])]
        if any(name == "subscription_id" for name, _, _ in columns):
            fks.append(sa.ForeignKeyConstraint(["subscription_id"], ["billing_subscriptions.id"]))
        op.create_table(table, *cols, *fks, sa.PrimaryKeyConstraint("id"))
        op.create_index(f"ix_{table}_merchant_id", table, ["merchant_id"])
    op.create_index(
        "ix_billing_invoices_provider_invoice_id",
        "billing_invoices",
        ["provider_invoice_id"],
        unique=True,
    )
    op.create_index(
        "ix_billing_payment_attempts_provider_payment_id",
        "billing_payment_attempts",
        ["provider_payment_id"],
        unique=True,
    )
    op.create_index(
        "ix_billing_refunds_provider_refund_id",
        "billing_refunds",
        ["provider_refund_id"],
        unique=True,
    )

    op.create_table(
        "razorpay_connections",
        _id("id"),
        sa.Column("merchant_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("provider_account_id", sa.String(120)),
        sa.Column("access_token_encrypted", sa.String(1000)),
        sa.Column("refresh_token_encrypted", sa.String(1000)),
        sa.Column("api_key_id", sa.String(160)),
        sa.Column("api_key_secret_encrypted", sa.String(1000)),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id"),
    )
    op.create_index(
        "ix_razorpay_connections_merchant_id", "razorpay_connections", ["merchant_id"], unique=True
    )
    op.create_index("ix_razorpay_connections_status", "razorpay_connections", ["status"])
    op.execute("ALTER TABLE razorpay_connections ENABLE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY razorpay_connections_merchant_isolation ON razorpay_connections
        USING (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)
        WITH CHECK (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)""")
    for table in (
        "billing_customers",
        "billing_subscriptions",
        "billing_entitlements",
        "billing_invoices",
        "billing_payment_attempts",
        "billing_refunds",
    ):
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'''CREATE POLICY {table}_merchant_isolation ON "{table}"
            USING (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)
            WITH CHECK (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)''')


def downgrade() -> None:
    for table in (
        "razorpay_connections",
        "billing_refunds",
        "billing_payment_attempts",
        "billing_invoices",
        "billing_entitlements",
        "billing_subscriptions",
        "billing_customers",
    ):
        op.execute(f'DROP POLICY IF EXISTS {table}_merchant_isolation ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    op.drop_table("razorpay_connections")
    for table in (
        "billing_refunds",
        "billing_payment_attempts",
        "billing_invoices",
        "billing_entitlements",
        "billing_events",
        "billing_subscriptions",
        "billing_customers",
        "billing_plans",
    ):
        op.drop_table(table)
