# Production Phases 2–3 evidence

Phase 2 and Phase 3 add the server-side billing boundary and merchant-owned
Razorpay collection boundary while leaving the frozen demo path intact.

## Delivered

- Versioned Starter, Growth, and Scale plan records with server-owned prices and
  active-invoice/seat entitlements. Provider plan IDs are immutable configuration
  mappings; enabling `RAZORPAY_SUBSCRIPTIONS_ENABLED` makes checkout create the
  corresponding platform subscription, while local/test defaults stay network-safe.
- Billing customer, subscription, event, entitlement, invoice, payment-attempt,
  and refund ledger tables. Billing webhooks verify the raw-body HMAC, deduplicate
  provider event IDs, persist payload hashes, and update subscription state with a
  seven-day `past_due` grace window before merchant suspension.
- Live invoice imports enforce the effective active-invoice cap; team invitations
  enforce seat caps; recovery does not send live reminders while billing is inactive.
- Per-merchant `razorpay_connections` records support encrypted OAuth/BYO credential
  storage, revocation, status, scope metadata, and redacted API responses. Live
  payment-link provisioning requires a connected merchant account and uses its BYO
  credentials; the platform Razorpay client is never reused for customer funds.
- Collection webhooks resolve the connected Razorpay account ID and reject a
  cross-tenant or revoked account before reconciliation. Existing event IDs remain
  replay-safe and payment state remains append-only/idempotent.
- PostgreSQL RLS policies cover every new merchant-owned billing and connection table.

## Verification

- `uv run ruff check app tests` — green.
- `uv run pytest -q --ignore=tests/integration` — **352 passed**.
- `uv run python -m compileall -q app alembic tests` — green.
- `uv run alembic upgrade head --sql` — generated PostgreSQL SQL through
  `f42b7d1c9e53` (`e31f6a9c7d42 → f42b7d1c9e53`).
- Unit coverage includes published plan values and encrypted credential round trips.

The runtime target remains the Docker-managed PostgreSQL database (`vasooli-db`,
database `vasooli`). The migration was reported as applied to that local database by
the parallel implementation session; this sandbox could validate the generated
PostgreSQL SQL and application checks but could not open the host Docker socket.

OAuth collection calls remain explicitly gated until the Razorpay Technology Partner
approval and token-backed adapter are configured; the documented BYO-key fallback is
implemented for the live payment-link path.
