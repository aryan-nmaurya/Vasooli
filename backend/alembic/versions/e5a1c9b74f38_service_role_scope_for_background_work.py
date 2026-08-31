"""let trusted background work read across tenants under an explicit service scope

Revision ID: e5a1c9b74f38
Revises: d4e5f6a7b890

Every merchant-isolation policy reads a transaction-local `app.merchant_id`, and a
request path sets it after authorization. Nothing sets it on the paths that have no
request and no single tenant:

* the daily recovery cycle, which walks every eligible invoice
* the Razorpay webhook, which must resolve a payment link to an invoice *before* it
  can know whose invoice it is
* the inbound-email correlator, which resolves a reply token the same way
* the delivery, closure, event and inbound retry sweeps
* the hourly payment-link reconciliation against Razorpay

Under the role the deployment actually connects as today — a Postgres superuser — this
was invisible, because superusers bypass row-level security unconditionally. Under the
role `scripts/create_app_role.sql` exists to create (NOSUPERUSER, NOBYPASSRLS), which is
the entire point of forcing RLS, `merchant_id = NULL` matches nothing and every one of
those queries returns zero rows. The cycle then reports a clean run having considered no
invoices, and a customer who paid is never reconciled because the webhook cannot find
the invoice. Both fail silently, with nothing to alarm on.

`billing_subscriptions` already carried this exact escape hatch, added when the billing
reconciliation worker hit the same wall. This extends the established pattern to the
remaining tenant tables rather than inventing a second mechanism.

The scope is deliberately narrow:

* `USING` only. Every `WITH CHECK` is left exactly as it was, so service scope can read
  across tenants but can never *write* a row into the wrong one.
* Transaction-local (`set_config(..., true)`), so it cannot survive a commit or leak
  through the connection pool into a request.
* Set only by `app.services.authorization.service_scope`, which no request path calls.

Three policies carry extra terms that must survive: `collection_ledger_entries` compares
as text rather than uuid, `oauth_states` also matches an unauthenticated callback by
state hash, and `billing_reconciliation_runs` allows a null (platform-wide) merchant.
They are rewritten explicitly rather than through the loop.
"""

from alembic import op

revision = "e5a1c9b74f38"
down_revision = "d4e5f6a7b890"
branch_labels = None
depends_on = None

_MATCH = "merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid"
_SERVICE = "current_setting('app.service_role', true) = 'true'"

#: Tables whose isolation policy is exactly `_MATCH` for both USING and WITH CHECK.
#: `billing_subscriptions` is absent: it already has the clause, plus a `webhook_mode`
#: term this migration must not drop.
PLAIN_TENANT_TABLES = (
    "audit_events",
    "billing_customers",
    "billing_entitlements",
    "billing_invoices",
    "billing_payment_attempts",
    "billing_refunds",
    "customers",
    "data_requests",
    "erp_connections",
    "erp_records",
    "erp_sync_runs",
    "erp_webhook_events",
    "integration_failures",
    "invoices",
    "merchant_invitations",
    "merchant_memberships",
    "merchant_usage_buckets",
    "razorpay_connections",
    "reminder_policy_versions",
    "roles",
    "sending_domains",
    "suppression_entries",
    "user_permission_overrides",
)

#: (table, USING without service scope, WITH CHECK) for the three special cases.
SPECIAL_TENANT_TABLES = (
    (
        "collection_ledger_entries",
        "merchant_id::text = current_setting('app.merchant_id', true)",
        "merchant_id::text = current_setting('app.merchant_id', true)",
    ),
    (
        "oauth_states",
        f"{_MATCH} OR state_hash = NULLIF(current_setting('app.oauth_state', true), '')",
        _MATCH,
    ),
    (
        "billing_reconciliation_runs",
        f"merchant_id IS NULL OR {_MATCH}",
        f"merchant_id IS NULL OR {_MATCH}",
    ),
)


def _recreate(table: str, using: str, with_check: str) -> None:
    policy = f"{table}_merchant_isolation"
    op.execute(f'DROP POLICY IF EXISTS {policy} ON "{table}"')
    op.execute(f'CREATE POLICY {policy} ON "{table}" USING ({using}) WITH CHECK ({with_check})')


def _apply(*, with_service_scope: bool) -> None:
    for table in PLAIN_TENANT_TABLES:
        using = f"{_MATCH} OR {_SERVICE}" if with_service_scope else _MATCH
        _recreate(table, using, _MATCH)
    for table, base_using, with_check in SPECIAL_TENANT_TABLES:
        using = f"({base_using}) OR {_SERVICE}" if with_service_scope else base_using
        _recreate(table, using, with_check)


def upgrade() -> None:
    _apply(with_service_scope=True)


def downgrade() -> None:
    _apply(with_service_scope=False)
