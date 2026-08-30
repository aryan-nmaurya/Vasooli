"""allow the trusted billing reconciliation worker to read all subscriptions"""

from alembic import op

revision = "b2c3d4e5f678"
down_revision = "b1c2d3e4f567"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        'DROP POLICY IF EXISTS billing_subscriptions_merchant_isolation ON "billing_subscriptions"'
    )
    op.execute("""CREATE POLICY billing_subscriptions_merchant_isolation ON "billing_subscriptions"
        USING (
            merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid
            OR current_setting('app.webhook_mode', true) = 'true'
            OR current_setting('app.service_role', true) = 'true'
        )
        WITH CHECK (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)""")


def downgrade() -> None:
    op.execute(
        'DROP POLICY IF EXISTS billing_subscriptions_merchant_isolation ON "billing_subscriptions"'
    )
    op.execute("""CREATE POLICY billing_subscriptions_merchant_isolation ON "billing_subscriptions"
        USING (
            merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid
            OR current_setting('app.webhook_mode', true) = 'true'
        )
        WITH CHECK (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)""")
