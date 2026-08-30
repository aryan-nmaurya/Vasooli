# Production implementation completion evidence

This document records repository-deliverable work against
`Docs/Production_Implementation_Plan.md`. Provider approval, production credentials,
DNS ownership, independent security review, and observed restore drills remain external
launch evidence and are not represented as completed by tests.

## Newly completed in this pass

- Razorpay Technology Partner OAuth authorization URL, one-time state, code exchange,
  refresh-token rotation, encrypted storage, and bearer-token payment-link client.
- Zoho OAuth authorization/code exchange/refresh lifecycle and a read-only Zoho Books
  incremental invoice adapter.
- Tally outbound edge-agent invoice adapter; the agent, not the Vasooli API, owns the
  local Tally XML/HTTP connection.
- Signed custom ERP webhook ingestion with event-id deduplication and replay-safe
  processing.
- DNS-over-HTTPS sending-domain challenge verification.
- Global outbound kill switch and daily safety quota.
- Durable billing reconciliation runs and a scheduled reconciliation job.
- TOTP MFA enrollment/verification/disable endpoints and short-lived password
  re-authentication challenges enforced on billing checkout, credential rotation,
  exports, deletion requests, and payment-account changes.
- Append-only merchant collection ledger entries keyed by verified provider event IDs,
  kept separate from Vasooli subscription billing records.
- Demo-controlled runtime settings are read from the persisted Postgres singleton on
  request paths, so API replicas do not diverge after a settings change.
- Standalone scheduler and worker entrypoints, with production Compose services for API,
  scheduler, and worker processes.
- Live registration, live sign-in, pricing, onboarding, billing, integrations, team,
  and settings routes in the Next.js application.
- Encrypted/off-host backup upload options now support KMS or AES256 server-side
  encryption and checksum artifacts.

## Verification

- Backend: `358 passed` excluding Docker/Postgres integration tests.
- Backend Ruff checks: green.
- Backend compilation: green.
- Alembic offline SQL: generated through `b2c3d4e5f678` (collection ledger, signed
  billing-webhook routing, and trusted reconciliation reads included).
- Frontend lint, TypeScript, Vitest: green (`107` tests).
- Frontend production build: green.

## Still external or environment-dependent

- Razorpay Technology Partner application approval and production OAuth credentials.
- Zoho app registration, organization consent, Tally agent deployment, and provider
  sandbox/production credentials.
- Docker-managed PostgreSQL migration application and full integration suite in the
  target environment. Apply the new head with `docker compose up -d db`, then
  `cd backend && uv run alembic upgrade head`; this desktop run could not access the
  Docker socket.
- DNS ownership, SPF/DKIM/DMARC, Resend sender/reputation setup.
- Production secret manager, TLS/domain, environment separation, SLO/alert wiring,
  independent penetration test, legal/DPA review, observed encrypted-backup restore
  with measured RPO/RTO, named incident/support owners, and paid pilot evidence.
