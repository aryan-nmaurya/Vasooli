# Production Phase 6 evidence

Phase 6 covers operations, security, and pilot readiness from
`Production_Implementation_Plan.md`.

## Delivered

- Added explicit `process_role` configuration (`api`, `scheduler`, `worker`) so
  worker deployments do not start an embedded scheduler; scheduler job history
  remains the operational source of truth.
- Added authenticated merchant readiness reporting for database state, recovery,
  payment-sync, retry, and heartbeat freshness.
- Added auditable export and deletion request workflows with merchant scoping,
  permission checks, append-only audit records, and operator-review metadata.
- Added safe PostgreSQL backup/restore scripts. Backup output is verified with
  `pg_restore --list`; restore requires an explicit target `DATABASE_URL` and never
  defaults to the application database.
- Added migration `c5d9e7f1a203` with forced RLS for data requests.

## Verification

- `uv run ruff check app tests alembic/versions` — green.
- `uv run pytest -q --ignore=tests/integration` — 356 passed at the current head.
- `uv run python -m compileall -q app alembic tests` — green.
- `uv run alembic upgrade head --sql` — PostgreSQL SQL generated through
  `c5d9e7f1a203`.

Remaining launch gates are external operational evidence: independent penetration
testing, an observed encrypted-backup restore with measured RPO/RTO, legal/DPA
review, named incident/support owners, and a controlled allow-listed pilot.
