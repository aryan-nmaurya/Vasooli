"""add durable billing reconciliation evidence"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f8a9b0c1d234"
down_revision = "e7f8a9b0c123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billing_reconciliation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("checked_count", sa.Integer(), nullable=False),
        sa.Column("drift_count", sa.Integer(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.String(length=1000)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_billing_reconciliation_runs_merchant_id", "billing_reconciliation_runs", ["merchant_id"]
    )
    op.create_index(
        "ix_billing_reconciliation_runs_status", "billing_reconciliation_runs", ["status"]
    )
    op.execute('ALTER TABLE "billing_reconciliation_runs" ENABLE ROW LEVEL SECURITY')
    op.execute("""CREATE POLICY billing_reconciliation_runs_merchant_isolation ON "billing_reconciliation_runs"
        USING (merchant_id IS NULL OR merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)
        WITH CHECK (merchant_id IS NULL OR merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)""")
    op.execute('ALTER TABLE "billing_reconciliation_runs" FORCE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.execute(
        'DROP POLICY IF EXISTS billing_reconciliation_runs_merchant_isolation ON "billing_reconciliation_runs"'
    )
    op.execute('ALTER TABLE "billing_reconciliation_runs" NO FORCE ROW LEVEL SECURITY')
    op.drop_table("billing_reconciliation_runs")
