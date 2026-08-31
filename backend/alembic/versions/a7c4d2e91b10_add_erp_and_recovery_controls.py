"""add ERP sync state and versioned recovery controls

Revision ID: a7c4d2e91b10
Revises: f42b7d1c9e53
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7c4d2e91b10"
down_revision: str | Sequence[str] | None = "c7d31a08b915"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id(name: str = "id") -> sa.Column:
    return sa.Column(name, sa.UUID(), nullable=False)


def _merchant_table(name: str, columns: list[sa.Column], *constraints: sa.Constraint) -> None:
    op.create_table(
        name,
        _id(),
        sa.Column("merchant_id", sa.UUID(), nullable=False),
        *columns,
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.PrimaryKeyConstraint("id"),
        *constraints,
    )
    op.create_index(f"ix_{name}_merchant_id", name, ["merchant_id"])
    op.execute(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'''CREATE POLICY {name}_merchant_isolation ON "{name}"
        USING (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)
        WITH CHECK (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)''')


def upgrade() -> None:
    _merchant_table(
        "erp_connections",
        [
            sa.Column("provider", sa.String(40), nullable=False),
            sa.Column("source_tenant", sa.String(160)),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("credentials_encrypted", sa.String(2000)),
            sa.Column("cursor", sa.String(500)),
            sa.Column("last_sync_at", sa.DateTime(timezone=True)),
            sa.Column("last_success_at", sa.DateTime(timezone=True)),
            sa.Column("freshness_deadline", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        ],
        sa.UniqueConstraint("merchant_id", "provider", name="uq_erp_connection_provider"),
    )
    op.create_index("ix_erp_connections_status", "erp_connections", ["status"])

    _merchant_table(
        "erp_sync_runs",
        [
            sa.Column("connection_id", sa.UUID(), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("cursor_before", sa.String(500)),
            sa.Column("cursor_after", sa.String(500)),
            sa.Column("imported_count", sa.Integer(), nullable=False),
            sa.Column("skipped_count", sa.Integer(), nullable=False),
            sa.Column("failed_count", sa.Integer(), nullable=False),
            sa.Column("error", sa.String(1000)),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.ForeignKeyConstraint(["connection_id"], ["erp_connections.id"]),
        ],
    )
    op.create_index("ix_erp_sync_runs_status", "erp_sync_runs", ["status"])

    _merchant_table(
        "erp_records",
        [
            sa.Column("connection_id", sa.UUID(), nullable=False),
            sa.Column("provider", sa.String(40), nullable=False),
            sa.Column("source_tenant", sa.String(160), nullable=False),
            sa.Column("record_type", sa.String(40), nullable=False),
            sa.Column("source_record_id", sa.String(180), nullable=False),
            sa.Column("source_version", sa.String(120)),
            sa.Column("source_updated_at", sa.DateTime(timezone=True)),
            sa.Column("tombstoned", sa.Boolean(), nullable=False),
            sa.Column("payload_hash", sa.String(64), nullable=False),
            sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["connection_id"], ["erp_connections.id"]),
        ],
        sa.UniqueConstraint(
            "merchant_id",
            "provider",
            "source_tenant",
            "record_type",
            "source_record_id",
            name="uq_erp_record_identity",
        ),
    )
    op.create_index("ix_erp_records_provider", "erp_records", ["provider"])

    _merchant_table(
        "integration_failures",
        [
            sa.Column("connection_id", sa.UUID(), nullable=False),
            sa.Column("sync_run_id", sa.UUID()),
            sa.Column("category", sa.String(50), nullable=False),
            sa.Column("source_record_id", sa.String(180)),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            sa.Column("error", sa.String(1000), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("next_retry_at", sa.DateTime(timezone=True)),
            sa.Column("resolved_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["connection_id"], ["erp_connections.id"]),
            sa.ForeignKeyConstraint(["sync_run_id"], ["erp_sync_runs.id"]),
        ],
    )

    _merchant_table(
        "reminder_policy_versions",
        [
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("tier_offsets", postgresql.JSONB(), nullable=False),
            sa.Column("cooldown_days", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("timezone", sa.String(80), nullable=False),
            sa.Column("channel", sa.String(30), nullable=False),
            sa.Column("sending_window", postgresql.JSONB(), nullable=False),
            sa.Column("pause_conditions", postgresql.JSONB(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_by_user_id", sa.UUID()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        ],
        sa.UniqueConstraint("merchant_id", "version", name="uq_policy_merchant_version"),
    )

    _merchant_table(
        "suppression_entries",
        [
            sa.Column("customer_id", sa.UUID()),
            sa.Column("email", sa.String(320)),
            sa.Column("reason", sa.String(40), nullable=False),
            sa.Column("source", sa.String(40), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        ],
    )
    op.create_index("ix_suppression_entries_email", "suppression_entries", ["email"])

    _merchant_table(
        "sending_domains",
        [
            sa.Column("domain", sa.String(255), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("verification_token", sa.String(160), nullable=False),
            sa.Column("dns_records", postgresql.JSONB(), nullable=False),
            sa.Column("verified_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        ],
        sa.UniqueConstraint("merchant_id", "domain", name="uq_sending_domain_merchant"),
    )
    op.create_index("ix_sending_domains_status", "sending_domains", ["status"])

    _merchant_table(
        "merchant_usage_buckets",
        [
            sa.Column("bucket_date", sa.Date(), nullable=False),
            sa.Column("sent_count", sa.Integer(), nullable=False),
            sa.Column("failed_count", sa.Integer(), nullable=False),
            sa.Column("quota", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        ],
        sa.UniqueConstraint("merchant_id", "bucket_date", name="uq_usage_merchant_day"),
    )


def downgrade() -> None:
    for table in (
        "merchant_usage_buckets",
        "sending_domains",
        "suppression_entries",
        "reminder_policy_versions",
        "integration_failures",
        "erp_records",
        "erp_sync_runs",
        "erp_connections",
    ):
        op.execute(f'DROP POLICY IF EXISTS {table}_merchant_isolation ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
        op.drop_table(table)
