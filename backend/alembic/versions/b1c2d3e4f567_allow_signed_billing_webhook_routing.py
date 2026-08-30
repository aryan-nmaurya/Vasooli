"""allow verified billing webhooks to resolve a tenant before RLS context exists"""

from alembic import op

revision = "b1c2d3e4f567"
down_revision = "a0b1c2d3e456"
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
        )
        WITH CHECK (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)""")


def downgrade() -> None:
    op.execute(
        'DROP POLICY IF EXISTS billing_subscriptions_merchant_isolation ON "billing_subscriptions"'
    )
    op.execute("""CREATE POLICY billing_subscriptions_merchant_isolation ON "billing_subscriptions"
        USING (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)
        WITH CHECK (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)""")
