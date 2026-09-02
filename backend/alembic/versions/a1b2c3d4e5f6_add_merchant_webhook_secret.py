"""Per-merchant Razorpay webhook secret.

Payment links are created on each merchant's own Razorpay account, so the webhooks
confirming those payments are signed with that merchant's webhook secret — not the
platform's. Verification had only ever tried the platform secret, so every merchant
webhook would fail its signature check and the payment would be picked up hours
later by the reconciliation sweep instead of in seconds.

Nullable: a merchant may connect before configuring a webhook, and the platform
secret still covers the demo account and any link still issued platform-side.

Revision ID: a1b2c3d4e5f6
Revises: f6b7c8d9e012
"""

import sqlalchemy as sa

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "f6b7c8d9e012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "razorpay_connections",
        sa.Column("webhook_secret_encrypted", sa.String(length=1000), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("razorpay_connections", "webhook_secret_encrypted")
