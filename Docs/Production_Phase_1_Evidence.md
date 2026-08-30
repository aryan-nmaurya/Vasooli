# Production Phase 1 evidence

Phase 1 adds the live tenancy and identity boundary described in the implementation
plan while preserving the frozen demo path.

## Delivered

- Live `users` identity with Argon2id password hashes, pending/active/suspended/deleted
  lifecycle, email verification, password reset, login throttling, session listing,
  logout-all, rotating refresh tokens, and refresh-token reuse detection.
- Merchant-scoped memberships, immutable system roles, granular permissions,
  invitations, reserved permission overrides/MFA factor storage, and append-only
  `audit_events`.
- Merchant mode/status/onboarding fields, with existing rows defaulted to explicit
  `demo` tenants and live registration dark behind `LIVE_REGISTRATION_ENABLED=false`.
- PostgreSQL RLS policies for customers, invoices, roles, memberships, invitations,
  and live audit events. The authorization dependency sets `app.merchant_id`
  transaction-locally and returns not-found for cross-tenant object IDs.
- Live invoice list/detail/import/provision routes, each permission-gated and scoped
  by `X-Merchant-ID`.
- Invoice identity fixes: `(merchant_id, invoice_number)` uniqueness, UUID reply
  tokens, UUID-derived live payment-link references, and token-derived live reply
  aliases. Demo aliases and references remain legacy-compatible.

## Verification

- `uv run ruff check app tests alembic/versions/e31f6a9c7d42_add_live_tenancy_and_identity.py` — green.
- `uv run pytest -q --ignore=tests/integration` — **349 passed**.
- `uv run python -m compileall -q app alembic tests/integration/test_live_identity.py` — green.
- `uv run alembic upgrade head --sql` — generated the complete upgrade SQL through
  revision `e31f6a9c7d42`.
- Integration tests are included in `tests/integration/test_live_identity.py` for
  registration/verification/login, tenant-safe duplicate invoice numbers, IDOR
  rejection, and refresh-family replay revocation.

Applying the migration and running the Postgres integration suite was not possible in
this session because localhost database access requires an escalation whose approval
quota was exhausted; the generated offline migration was validated instead.
