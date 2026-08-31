# Production implementation completion evidence

This document records repository-deliverable work against
`Docs/Production_Implementation_Plan.md`. Provider approval, production credentials,
DNS ownership, independent security review, and observed restore drills remain external
launch evidence and are not represented as completed by tests.

> Updated 2026-08-31. This is implementation evidence, not a production-launch
> approval. `README.md` §14 is the current blocker list.

## Newly completed in this pass

- Razorpay Technology Partner OAuth authorization URL, one-time state, code exchange,
  refresh-token rotation, encrypted storage, and bearer-token payment-link client.
- Zoho OAuth authorization/code exchange/refresh lifecycle and a read-only Zoho Books
  invoice adapter, including organization resolution for single-organization accounts.
- Zoho, CSV, and signed custom-feed ingestion. Tally is intentionally not advertised
  until a separately deployable edge agent exists.
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
- Locked, observable automatic ERP polling for configured provider connections.
- Provider-backed live verification/password-reset email and complete browser routes for
  verification, forgotten password, and reset completion.
- Live registration, live sign-in, pricing, onboarding, billing, integrations, team,
  and settings routes in the Next.js application.
- Encrypted/off-host backup upload options now support KMS or AES256 server-side
  encryption and checksum artifacts.

## Verification

- Backend: `977 passed`, including the local PostgreSQL integration suite on a fresh
  database created and migrated from zero.
- Backend Ruff checks: green.
- Backend compilation: green.
- Alembic head: `f6b7c8d9e012`; downgrade to `e5a1c9b74f38` and re-upgrade to head:
  green on the fresh release-test database.
- Frontend lint, TypeScript, Vitest: green (`116` tests).
- Frontend production build: green with Next.js webpack; Turbopack cannot bind its
  local worker port in this execution sandbox.

## Still external or environment-dependent

- Razorpay Technology Partner application approval and production OAuth credentials.
- Zoho app registration, organization consent, and provider sandbox/production
  credentials. A future Tally offering still requires a separately deployable agent.
- Target-environment PostgreSQL migration, restore, restart, and failover evidence.
- DNS ownership, SPF/DKIM/DMARC, Resend sender/reputation setup.
- Production secret manager, TLS/domain, environment separation, SLO/alert wiring,
  independent penetration test, legal/DPA review, observed encrypted-backup restore
  with measured RPO/RTO, named incident/support owners, and paid pilot evidence.
- ERP updates/cancellations/payments/credit notes, provider refund/chargeback events,
  trial expiry, per-merchant sender identity, and the live merchant recovery workspace
  are implemented and covered by release-completion tests. Production credentials,
  provider approvals, DNS publication, and a real pilot remain external exit criteria.
