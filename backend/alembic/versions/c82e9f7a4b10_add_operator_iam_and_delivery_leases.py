"""add operator IAM and leased reminder delivery

Revision ID: c82e9f7a4b10
Revises: b71c1a0e4d2f
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c82e9f7a4b10"
down_revision: str | Sequence[str] | None = "b71c1a0e4d2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operator_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "failed_login_attempts >= 0",
            name="ck_operator_accounts_failed_login_attempts",
        ),
        sa.CheckConstraint(
            "role IN ('admin', 'operator', 'auditor')",
            name="ck_operator_accounts_role",
        ),
        sa.CheckConstraint(
            "session_version >= 1",
            name="ck_operator_accounts_session_version",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_operator_accounts_username",
        "operator_accounts",
        ["username"],
        unique=True,
    )

    op.add_column(
        "reminders",
        sa.Column("delivery_state", sa.String(), server_default="pending", nullable=False),
    )
    op.add_column("reminders", sa.Column("lease_token", sa.String(), nullable=True))
    op.add_column(
        "reminders",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_reminders_delivery_state",
        "reminders",
        "delivery_state IN ('pending', 'processing', 'sent', 'failed', 'dead')",
    )
    op.execute(
        """
        UPDATE reminders
        SET delivery_state = CASE
            WHEN sent_at IS NOT NULL THEN 'sent'
            WHEN attempt_count >= 5 OR next_retry_at IS NULL THEN 'dead'
            ELSE 'failed'
        END
        """
    )
    op.alter_column("reminders", "delivery_state", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_reminders_delivery_state", "reminders", type_="check")
    op.drop_column("reminders", "lease_expires_at")
    op.drop_column("reminders", "lease_token")
    op.drop_column("reminders", "delivery_state")
    op.drop_index("ix_operator_accounts_username", table_name="operator_accounts")
    op.drop_table("operator_accounts")
