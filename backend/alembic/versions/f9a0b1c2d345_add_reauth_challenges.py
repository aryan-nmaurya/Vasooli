"""add short-lived re-authentication challenges"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f9a0b1c2d345"
down_revision = "f8a9b0c1d234"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reauth_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_reauth_challenges_token_hash", "reauth_challenges", ["token_hash"])
    op.create_index("ix_reauth_challenges_expires_at", "reauth_challenges", ["expires_at"])


def downgrade() -> None:
    op.drop_table("reauth_challenges")
