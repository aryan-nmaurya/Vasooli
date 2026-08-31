"""add auditable export and deletion requests

Revision ID: c5d9e7f1a203
Revises: b3e8f1a2c904
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c5d9e7f1a203"
down_revision = "b3e8f1a2c904"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("merchant_id", sa.UUID(), nullable=False),
        sa.Column("requested_by_user_id", sa.UUID(), nullable=False),
        sa.Column("request_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column("artifact_uri", sa.String(1000)),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_data_requests_merchant_id", "data_requests", ["merchant_id"])
    op.create_index("ix_data_requests_request_type", "data_requests", ["request_type"])
    op.create_index("ix_data_requests_status", "data_requests", ["status"])
    op.execute('ALTER TABLE "data_requests" ENABLE ROW LEVEL SECURITY')
    op.execute("""CREATE POLICY data_requests_merchant_isolation ON "data_requests"
        USING (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)
        WITH CHECK (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)""")
    op.execute('ALTER TABLE "data_requests" FORCE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS data_requests_merchant_isolation ON "data_requests"')
    op.execute('ALTER TABLE "data_requests" NO FORCE ROW LEVEL SECURITY')
    op.drop_table("data_requests")
