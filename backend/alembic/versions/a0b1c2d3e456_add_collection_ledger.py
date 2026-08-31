"""add append-only merchant collection ledger"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a0b1c2d3e456"
down_revision = "f9a0b1c2d345"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collection_ledger_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("merchant_id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=True),
        sa.Column("provider_event_id", sa.String(length=180), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="INR", nullable=False),
        sa.Column("provider_reference", sa.String(length=180), nullable=True),
        sa.Column("payload", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "provider_event_id", name="uq_collection_ledger_event"),
    )
    op.create_index(
        "ix_collection_ledger_entries_provider_event_id",
        "collection_ledger_entries",
        ["provider_event_id"],
    )
    op.create_index(
        "ix_collection_ledger_entries_recorded_at", "collection_ledger_entries", ["recorded_at"]
    )
    op.execute('ALTER TABLE "collection_ledger_entries" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "collection_ledger_entries" FORCE ROW LEVEL SECURITY')
    op.execute("""CREATE POLICY collection_ledger_entries_merchant_isolation ON "collection_ledger_entries"
        USING (merchant_id::text = current_setting('app.merchant_id', true))
        WITH CHECK (merchant_id::text = current_setting('app.merchant_id', true))""")


def downgrade() -> None:
    op.execute(
        'DROP POLICY IF EXISTS collection_ledger_entries_merchant_isolation ON "collection_ledger_entries"'
    )
    op.execute('ALTER TABLE "collection_ledger_entries" NO FORCE ROW LEVEL SECURITY')
    op.drop_index(
        "ix_collection_ledger_entries_recorded_at", table_name="collection_ledger_entries"
    )
    op.drop_index(
        "ix_collection_ledger_entries_provider_event_id", table_name="collection_ledger_entries"
    )
    op.drop_table("collection_ledger_entries")
