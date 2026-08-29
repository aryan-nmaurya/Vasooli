"""add verified inbound messages

Revision ID: b71c1a0e4d2f
Revises: 2f47bba4f219
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b71c1a0e4d2f"
down_revision: str | Sequence[str] | None = "2f47bba4f219"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inbound_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("provider_event_id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("sender", sa.String(), nullable=False),
        sa.Column("recipient", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("body_text", sa.String(), nullable=False),
        sa.Column("in_reply_to", sa.String(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("signature_verified", sa.Boolean(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_error", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inbound_messages_invoice_id", "inbound_messages", ["invoice_id"])
    op.create_index(
        "ix_inbound_messages_provider_event_id",
        "inbound_messages",
        ["provider_event_id"],
        unique=True,
    )
    op.create_index(
        "ix_inbound_messages_message_id", "inbound_messages", ["message_id"], unique=True
    )
    op.create_index("ix_inbound_messages_received_at", "inbound_messages", ["received_at"])


def downgrade() -> None:
    op.drop_index("ix_inbound_messages_received_at", table_name="inbound_messages")
    op.drop_index("ix_inbound_messages_message_id", table_name="inbound_messages")
    op.drop_index("ix_inbound_messages_provider_event_id", table_name="inbound_messages")
    op.drop_index("ix_inbound_messages_invoice_id", table_name="inbound_messages")
    op.drop_table("inbound_messages")
