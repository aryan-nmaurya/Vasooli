# Production Phases 4–5 evidence

> Historical phase evidence, updated 2026-08-31 where later work superseded it.

## Phase 4 — ERP adapter platform

- Added tenant-owned `erp_connections`, cursor-based `erp_sync_runs`, canonical
  `erp_records`, and replayable `integration_failures` tables.
- Added a provider-neutral `CanonicalInvoice`/`SyncPage` contract, a deterministic
  custom fixture adapter, credential-backed Zoho/Tally adapters, and scheduled polling
  under per-merchant RLS context.
- Sync orchestration persists source identity, payload hashes, tombstones, cursors,
  freshness deadlines, partial counts, and retryable failures. Every record identity
  includes merchant, provider, source tenant, type, and source ID.
- Added merchant-scoped integration connect/list/sync/run APIs with permission and
  billing gates.

## Phase 5 — recovery and merchant controls

- Added immutable `reminder_policy_versions` with save-time validation. The default
  remains 3/10/21 with cooldown 7; the supported 3/7/14 preset uses cooldown 4.
  Validation names the exact offending tier pair and enforces the platform floor.
- Recovery reads the active merchant policy while retaining frozen demo defaults.
- Added suppression entries for unsubscribe, hard bounce, abuse complaint, legal hold,
  and merchant blocks; outbound dispatch checks suppression before provider calls.
- Added per-merchant daily usage buckets and quota enforcement, plus sending-domain
  verification state/DNS challenge records.
- Added policy, suppression, domain, and usage controls APIs with audit/permission
  boundaries. New tables use PostgreSQL RLS and forced-RLS follow-up migration.

## Verification

- `uv run ruff check app tests alembic/versions` — green.
- `uv run pytest -q --ignore=tests/integration` — **355 passed**.
- `uv run python -m compileall -q app alembic tests` — green.
- `uv run alembic upgrade head --sql` — PostgreSQL SQL generated through
  `b3e8f1a2c904` (`f42b7d1c9e53 → c7d31a08b915 → a7c4d2e91b10 → b3e8f1a2c904`).

Provider credentials, a deployed Tally edge agent, and live provider exercises remain
explicit integration gates. ERP source updates, cancellations, provider payments, and
credit notes are not yet folded into canonical ledger state; README §14 records this
as a financial-integrity blocker.
