"""add replay-safe custom ERP webhook envelopes"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e7f8a9b0c123"
down_revision = "d6e4f7a8b901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "erp_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_event_id", sa.String(length=180), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("signature_verified", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("processing_error", sa.String(length=1000)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.ForeignKeyConstraint(["connection_id"], ["erp_connections.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id", "provider_event_id", name="uq_erp_webhook_connection_event"
        ),
    )
    op.create_index(
        "ix_erp_webhook_events_provider_event_id", "erp_webhook_events", ["provider_event_id"]
    )
    op.execute('ALTER TABLE "erp_webhook_events" ENABLE ROW LEVEL SECURITY')
    op.execute("""CREATE POLICY erp_webhook_events_merchant_isolation ON "erp_webhook_events"
        USING (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)
        WITH CHECK (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)""")
    op.execute('ALTER TABLE "erp_webhook_events" FORCE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.execute(
        'DROP POLICY IF EXISTS erp_webhook_events_merchant_isolation ON "erp_webhook_events"'
    )
    op.execute('ALTER TABLE "erp_webhook_events" NO FORCE ROW LEVEL SECURITY')
    op.drop_table("erp_webhook_events")
