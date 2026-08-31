"""index the foreign keys on hot paths and RLS predicates

Revision ID: d4e5f6a7b890
Revises: c3d4e5f6a789

Fourteen foreign keys were unindexed. Most are on low-volume billing tables and can
stay that way; these four cannot, for two different reasons.

**Sequential scans on a per-send path.** `suppression_entries.customer_id` is read on
every outbound reminder to decide whether the recipient is on the do-not-contact list.
A suppression list only grows — every bounce, complaint and unsubscribe adds a row and
nothing removes them — so an unindexed lookup degrades exactly as a merchant's list
becomes worth checking.

**Sequential scans inside a security policy.** Row-level security adds
`merchant_id = current_setting(...)` to every statement against a tenant table. Without
an index on that column the policy turns each query into a full scan, and the cost is
paid on reads the application never sees as expensive.

Postgres also needs these when validating a parent DELETE: without a child index it
scans the whole child table to prove no row references the row being removed.
"""

from alembic import op

revision = "d4e5f6a7b890"
down_revision = "c3d4e5f6a789"
branch_labels = None
depends_on = None

INDEXES = (
    # Read on every send.
    ("ix_suppression_entries_customer_id", "suppression_entries", "customer_id"),
    # RLS predicate columns.
    ("ix_erp_webhook_events_merchant_id", "erp_webhook_events", "merchant_id"),
    ("ix_oauth_states_merchant_id", "oauth_states", "merchant_id"),
    # Ledger lookups are per invoice.
    ("ix_collection_ledger_entries_invoice_id", "collection_ledger_entries", "invoice_id"),
    # Failure triage walks these from the connection that produced them.
    ("ix_integration_failures_connection_id", "integration_failures", "connection_id"),
    ("ix_erp_sync_runs_connection_id", "erp_sync_runs", "connection_id"),
)


def upgrade() -> None:
    for name, table, column in INDEXES:
        op.create_index(name, table, [column])


def downgrade() -> None:
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
