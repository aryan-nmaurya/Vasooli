"""force RLS on Phase 4–5 merchant-owned tables

Revision ID: b3e8f1a2c904
Revises: a7c4d2e91b10
"""

from alembic import op

revision = "b3e8f1a2c904"
down_revision = "a7c4d2e91b10"
branch_labels = None
depends_on = None

TABLES = (
    "erp_connections",
    "erp_sync_runs",
    "erp_records",
    "integration_failures",
    "reminder_policy_versions",
    "suppression_entries",
    "sending_domains",
    "merchant_usage_buckets",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
