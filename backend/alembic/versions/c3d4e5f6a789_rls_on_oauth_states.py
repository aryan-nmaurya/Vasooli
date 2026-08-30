"""force RLS on oauth_states

Revision ID: c3d4e5f6a789
Revises: b2c3d4e5f678

`oauth_states` carries `merchant_id` but was the one merchant-owned table left
without row-level security, which contradicts the plan's first invariant: every
merchant-owned row is isolated by RLS, and unknown ownership fails closed.

Low severity on its own — the raw state is never stored, only its hash, so reading
the table does not yield a usable state. But the invariant is the thing that makes
"we forgot a filter somewhere" survivable, and an exception nobody wrote down is how
that erodes.

The callback consumes a state *before* it knows which merchant it belongs to, so
plain isolation would deadlock it. Same shape as the invitation-token and
billing-webhook policies already in this schema: allow the lookup that resolves the
tenant, then let ordinary isolation apply once `app.merchant_id` is set.

`reauth_challenges` is deliberately excluded — it is keyed to a user, not a merchant,
so there is no merchant column to isolate on.
"""

from alembic import op

revision = "c3d4e5f6a789"
down_revision = "b2c3d4e5f678"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE "oauth_states" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "oauth_states" FORCE ROW LEVEL SECURITY')
    op.execute("""CREATE POLICY oauth_states_merchant_isolation ON "oauth_states"
        USING (
            merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid
            OR state_hash = NULLIF(current_setting('app.oauth_state', true), '')
        )
        WITH CHECK (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)""")


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS oauth_states_merchant_isolation ON "oauth_states"')
    op.execute('ALTER TABLE "oauth_states" NO FORCE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "oauth_states" DISABLE ROW LEVEL SECURITY')
