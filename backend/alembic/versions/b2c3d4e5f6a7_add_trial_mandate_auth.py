"""Record the mandate-verification payment and its refund.

Starting a trial requires authorising an Autopay mandate, and a mandate cannot be
validated for nothing — the bank or UPI app confirms the customer approved recurring
debits by taking a small payment. That amount is charged at authorisation and
refunded once the subscription reports itself authenticated.

Both ids are stored so the refund is issued exactly once. The authenticated webhook
can be delivered more than once, and refunding twice returns real money twice.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""

import sqlalchemy as sa

from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "billing_subscriptions",
        sa.Column("auth_payment_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "billing_subscriptions",
        sa.Column("auth_refund_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "billing_subscriptions",
        sa.Column("auth_amount_paise", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("billing_subscriptions", "auth_amount_paise")
    op.drop_column("billing_subscriptions", "auth_refund_id")
    op.drop_column("billing_subscriptions", "auth_payment_id")
