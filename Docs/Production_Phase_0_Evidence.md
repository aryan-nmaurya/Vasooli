# Production Phase 0 evidence

This file tracks the acceptance evidence for Phase 0 of
`Production_Implementation_Plan.md`. It distinguishes repository-complete work from
external launch actions so a green test cannot be mistaken for an OAuth approval or a
staging rehearsal.

## Demo freeze

| Evidence | State | Location |
|---|---|---|
| Pre-production source tag | Complete locally | `demo-freeze-2026-08-30` at `47243ee` |
| Eight-invoice stable ledger | Complete | `backend/tests/golden/demo/ledger.json` |
| Full demo API snapshots | Complete (45 contracts) | `backend/tests/golden/demo/api_responses.json` |
| Eight ten-check policy traces | Complete | `backend/tests/golden/demo/policy_traces.json` |
| All current demo screens | Complete (9 routes) | `Docs/assets/demo-baseline/` |
| Artifact/source integrity manifest | Complete | `Docs/demo-freeze-manifest.json` |
| Merge-blocking workflow job | Implemented | `demo-regression (required)` in `.github/workflows/ci.yml` |

The backend snapshots normalize only generated UUIDs and wall-clock timestamps.
Invoice numbers, customer identity, amounts, overdue offsets, cadence results, copy,
statuses, response fields, and policy checks remain exact. Updating a golden requires
the explicit `UPDATE_DEMO_GOLDENS=1` opt-in. Updating screenshots or a frozen frontend
surface requires an explicit manifest re-baseline.

Run the gate locally:

```bash
cd backend
uv run pytest -q tests/integration/test_demo_golden.py
cd ..
node scripts/verify_demo_freeze.mjs
```

Branch protection must require the named `demo-regression (required)` check. The
workflow supplies the check; the repository host setting is external to this codebase.

## Query and singleton inventory

The current application is single-merchant. Phase 1 must close every item below before
the corresponding endpoint or worker can be enabled for live merchants.

| Surface | Known unscoped ownership reads |
|---|---|
| Dashboard API | Queue, customer/link maps, disputes, promises, audit, exceptions, and dispute lists in `app/api/dashboard.py` |
| Invoice API | Detail/list customer and link lookup in `app/api/invoices.py` |
| Payment API | External-payment and reconciliation lookup in `app/api/payments.py` |
| Inbound webhook | Sender/alias correlation scans invoices globally in `app/api/webhooks.py` |
| Exports/metrics | Invoice, customer, and link scans in `app/services/exports.py` and `app/services/metrics.py` |
| Recovery | Promise, invoice, merchant, reminder, and link scans in `app/services/recovery.py` |
| Delivery/replies | Reminder retry and invoice-number lookup in `app/services/messaging.py` and `app/services/replies.py` |
| Provisioning/reconciliation | Global pending-link and provider-reference lookup in `app/services/provisioning.py` and `app/services/reconciliation.py` |
| Sync/closure | Global open-link and invoice-number lookup in `app/services/sync.py` and `app/services/closure.py` |
| Demo runtime singleton | Module-level clock/email overrides in `app/core/clock.py` and `app/core/runtime.py`, mirrored to `demo_settings` |

`app/services/ingestion.py` is the existing exception: its invoice and customer
deduplication already includes `merchant_id`. Its fallback selection of the first
merchant remains a live-mode blocker.

## Hard-coded policy and provider inventory

| Contract | Current owner | Phase requirement |
|---|---|---|
| Cadence 3 / 10 / 21 | `app/core/constants.py` | Preserve for demo; introduce immutable live merchant policy versions |
| Cooldown 7 days | `app/core/constants.py`, policy/explanation/eval callers | Preserve for demo; split live default 7 from platform floor 3 |
| Automated cap 3 | Constants plus invoice/reminder/promise constraints | Preserve for demo; validate live policy against platform cap |
| Razorpay collection credentials | `Settings` and `integrations/razorpay_client.py` | Keep demo keys; add separate subscription and merchant-connection credentials |
| Razorpay webhook secret | `Settings`, `api/webhooks.py` | Split billing vs merchant collection endpoints and secrets |
| Resend sending/inbound/delivery | `Settings`, messaging and webhook adapters | Keep demo redirect/provider; add merchant sender verification and live provider isolation |
| Gemini models/key | `Settings`, `app/ai/client.py` | No tenant authority; retain deterministic fallback and audit provenance |
| Demo flags | `DEMO_CONTROLS_ENABLED`, `DEMO_TIME_OFFSET_DAYS`, reviewer access, simulated replies | Keep behind the explicit demo boundary; never treat as live authorization |

No credential values are recorded in this inventory or in the golden fixtures.

## External and operational gates

These are intentionally not marked complete by source changes:

- [ ] Push the local freeze tag to the canonical remote after review.
- [ ] Configure branch protection to require `demo-regression (required)`.
- [ ] Submit the Razorpay Technology Partner OAuth application and record its tracking reference.
- [ ] Provision production-like staging with isolated database, domains, queues, storage, and provider test credentials.
- [ ] Connect error tracking and attach a verified test event.
- [ ] Rehearse rollback from the first production feature flag and attach observed evidence.

Phase 1 must not start on a release branch until these ownership/evidence items are
closed or explicitly risk-accepted by the named launch owner.
