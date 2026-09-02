"""row-level security on the eight invoice-child tables

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a7

Eight tables hang off `invoices` by `invoice_id` and carry no `merchant_id`, so the
forced-RLS sweep that covered every merchant-owned table skipped them. Proven under a
NOBYPASSRLS role with no tenant context — the exact role production runs as:

    invoices      = 0     <- policy works
    customers     = 0     <- policy works
    reminders     = 19    <- visible
    payment_links = 2     <- visible
    audit_logs    = 235   <- visible

Not currently exploitable: every route that takes a child id re-loads the parent
through `get_scoped_object(..., context.merchant.id)`. This restores the database
backstop so that a future endpoint cannot quietly become an IDOR — the invariant is
what makes "somebody forgot a filter" survivable.

No new column and no backfill: `invoice_id IN (SELECT id FROM invoices)` is
self-scoping, because `invoices` is itself under forced RLS and the subquery is
evaluated with the caller's policies applied.

Two details that are easy to get wrong, and are handled explicitly:

* `audit_logs` and `email_events` have a NULLABLE `invoice_id`. Those rows are
  platform-level — in practice only rejected webhook signatures, which name no
  tenant. They are readable under service scope rather than by any tenant, but they
  remain WRITABLE from every path, because the webhook rejection path records one
  with no merchant context at all and must not start failing.

* `WITH CHECK` deliberately does NOT reuse the `USING` subquery. Under
  `service_scope` the invoices subquery widens to every tenant, so a shared
  expression would have let background work write a child row against somebody
  else's invoice. Matching `app.merchant_id` directly preserves the existing
  invariant: service scope reads across tenants and writes into none.

`audit_logs` also carries an append-only trigger; nothing here touches it, and the
`WITH CHECK` above still admits the inserts both the request and background paths
make.
"""

from alembic import op

revision = "c4d5e6f7a8b9"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None

_SERVICE = "current_setting('app.service_role', true) = 'true'"

#: Readable when the parent invoice is visible to the caller.
_VISIBLE_PARENT = "invoice_id IN (SELECT id FROM invoices)"

#: Writable only against an invoice owned by the tenant currently set. Not derived
#: from the subquery above — see the module docstring.
_OWNED_PARENT = (
    "invoice_id IN ("
    " SELECT id FROM invoices"
    " WHERE merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid"
    ")"
)

#: `invoice_id` is NOT NULL here, so every row belongs to exactly one tenant.
CHILD_TABLES = (
    "dispute_cases",
    "external_payments",
    "inbound_messages",
    "payment_links",
    "promises",
    "reminders",
)

#: `invoice_id` is nullable here; a null row is platform-level and owned by nobody.
NULLABLE_CHILD_TABLES = (
    "audit_logs",
    "email_events",
)

ALL_TABLES = CHILD_TABLES + NULLABLE_CHILD_TABLES


def upgrade() -> None:
    for table in CHILD_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f"""CREATE POLICY {table}_invoice_isolation ON "{table}"
            USING ({_VISIBLE_PARENT} OR {_SERVICE})
            WITH CHECK ({_OWNED_PARENT})""")

    for table in NULLABLE_CHILD_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f"""CREATE POLICY {table}_invoice_isolation ON "{table}"
            USING (
                {_SERVICE}
                OR (invoice_id IS NOT NULL AND {_VISIBLE_PARENT})
            )
            WITH CHECK (
                invoice_id IS NULL
                OR {_OWNED_PARENT}
            )""")


def downgrade() -> None:
    for table in ALL_TABLES:
        op.execute(f'DROP POLICY IF EXISTS {table}_invoice_isolation ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
