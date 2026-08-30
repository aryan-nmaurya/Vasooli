# Vasooli production implementation plan

**Status:** Implementation blueprint; the existing demo remains the reference experience until each production phase passes its acceptance gates.

**Product decision:** Vasooli will contain two deliberately separate experiences:

1. **Demo mode** – the current guided/demo accounts (Aryan, Mentor, reviewer) and deterministic sample data. Demo activity never contacts a real customer, creates a real collection link, or changes production data.
2. **Live mode** – registered merchants, real ERP data, real customer email, real payment collection, paid subscription entitlements, and tenant-scoped users.

The first production release must make the boundary visible in the UI and enforce it in the API, database, workers, email provider, payment provider, and deployment configuration. A mode switch must never be a shortcut around authorization or billing.

## 0. Demo freeze — non-negotiable

**The demo is frozen. Its behaviour on the day production work starts is the behaviour it must still have on the day production ships.** Everything in this plan is additive to the demo, never a modification of it.

This is not a preference. The demo is the pitch, the reviewer experience, and the only currently-working proof that the recovery engine does what it claims. A production migration that quietly changes a tier boundary, a seeded invoice, or a reviewer login has destroyed the thing the production system is being built to sell.

### What "frozen" covers exactly

| Frozen surface | Must remain byte-identical in behaviour |
|---|---|
| Demo credentials | The existing operator, mentor, and one-click reviewer logins, with their current roles |
| Seeded ledger | The eight demo invoices, their amounts, customers, overdue offsets, and narrative purpose |
| Cadence | 3 / 10 / 21 days, cooldown 7, cap 3 — the demo never adopts the new default or any merchant policy |
| Reviewer settings panel | Time machine, "send reminders to", and their audit rows |
| Demo routes and copy | `/guide`, `/login` reviewer path, runtime banner text, honesty tables |
| Safety posture | Redirected email, Razorpay test mode, simulated replies disabled |
| `scripts/demo_reset.py` | Same eight invoices, same output, same behaviour |

### How the freeze is enforced

1. **Capture golden fixtures before any production commit** (Phase 0, first task): full API response snapshots for every demo endpoint, the seeded ledger as a stable dump, a recorded policy-decision trace for each of the eight invoices, and screenshots of every demo screen. These become the regression oracle. Capturing them *after* migration work starts is worthless.
2. **A demo regression suite runs on every commit and blocks merge on failure.** Not a nightly job, not a pre-release check — a required CI gate, in the same class as the existing architecture tests.
3. **Additive-only rule for shared code.** Production work extends shared services through new arguments with demo-preserving defaults. Any change whose diff alters demo behaviour is rejected, not "verified by hand".
4. **The demo is feature-frozen.** No new demo features while production work is in flight. A bug fix that changes demo behaviour requires re-baselining the fixtures as a deliberate, reviewed act.
5. **Schema migrations must be transparent to the demo.** Expand/contract only: new columns arrive nullable or defaulted, the demo's rows are backfilled into an explicit demo merchant, and the demo's queries return exactly what they returned before.
6. **Rollback is always available.** The demo release is tagged before Phase 0 and every phase is behind a feature flag, so any production change can be switched off without reverting the demo.

### The demo's isolation model — decided

The demo stays **in the current database as an explicit demo merchant row**, isolated by row-level security and an `is_demo` flag. It does **not** move to a separate database or schema.

This resolves a contradiction in the earlier draft, which asked for a separate demo database in §2 while Phase 1 described backfilling into a demo merchant. Both cannot be true, and moving the demo to a new data plane would itself be a change to the demo — exactly what this section forbids. Separate *provider* isolation is retained and already exists today: demo keeps its own Razorpay test keys, its own sending configuration, and redirected email.

## 1. Reference material and migration decision

The attached `Drug-Supply-Management` project was reviewed as reference material, not as an instruction to change Vasooli’s domain.

### Concepts to port

- Email-first `User` identity with verified/active state.
- Tenant membership (`MerchantMembership`), role assignment, granular permission codenames, and invitation/enrollment tokens.
- Fail-closed tenant/object scoping and cross-tenant regression tests.
- Separation-of-duties checks for sensitive actions.
- Immutable release, backup/restore, incident, access-review, and launch-evidence discipline.

### Files and concepts not to copy

- The Django/DRF models, serializers, views, URLs, and migrations; Vasooli is FastAPI + SQLModel + Alembic.
- Pharmacy, clinical, patient, inventory, procurement, controlled-drug, and AML-specific domain rules.
- Pharmacy-specific JWT/CSRF implementation and assumptions about a pharmacy foreign key.
- Any source code that silently broadens a queryset or assumes one tenant.

The implementation should re-create the useful contracts in Vasooli modules and tests. Mechanical file migration would introduce framework and domain mismatches and is not safe for a paid system.

## 2. Non-negotiable production invariants

- Every merchant-owned row has a non-null `merchant_id`; every repository/service method requires an authorized merchant context.
- Tenant filtering is applied in the service/repository layer and enforced with PostgreSQL row-level security (RLS) in production. Missing/unknown ownership fails closed.
- A user can only act through an active merchant membership and an explicit permission; role names are never authorization logic.
- Demo and live have separate cookies, queues, object storage prefixes, provider credentials, webhook endpoints, and observability dimensions. They **share one database**, isolated by row-level security and an explicit demo merchant — see §0 for why moving the demo to a separate data plane is forbidden.
- Demo behaviour is frozen and CI-enforced (§0). Any change that alters a demo response, decision, or seeded row fails the build.
- No live customer email, payment link, or ERP write is permitted while a merchant is in demo mode, has a suspended subscription, or has not completed the required setup checks.
- Reminder sends are idempotent, auditable, rate limited, cancellable, and stopped when payment, dispute, promise, opt-out, bounce, or suppression rules require it.
- Incoming webhooks and ERP records are idempotent and replay-safe; out-of-order events cannot regress state.
- Billing entitlements are derived from verified Razorpay webhook state, not from a browser redirect.
- Existing demo behavior is regression-tested before every schema, auth, worker, or frontend change.

## 3. Target architecture

```text
Browser (Demo or Live)
        |
API gateway / TLS / rate limits
        |
FastAPI application ── PostgreSQL (RLS, audit, outbox, billing, ERP state)
        |                         |
        |                         +─ object storage (exports, evidence, encrypted)
        +─ enqueue durable jobs ── workers
                                    ├─ ERP sync/webhook worker
                                    ├─ recovery/policy worker
                                    ├─ email delivery worker
                                    └─ billing/reconciliation worker

ERP adapters (Zoho, Tally edge agent, custom REST/webhook/SFTP)
Razorpay platform account (Vasooli subscription billing)
Razorpay merchant accounts (customer collection links; one per merchant)
Email provider (Resend or equivalent; verified sending domain)
```

Keep the current deterministic policy engine, durable reminder outbox/leases, signed webhooks, promise/dispute handling, and reconciliation services. Expand them behind merchant-aware interfaces rather than replacing them in one rewrite.

## 4. Product and access flow

### Public routes

- `/` – value proposition and two clear CTAs: **Try demo** and **Start live account**.
- `/pricing` – the three paid plans, invoice definition, included limits, taxes, support, and cancellation terms.
- `/demo` – isolated demo entry; links to the existing demo credentials/modes without exposing live registration data.
- `/register` – live registration (email, password, legal business name, country/time zone, acceptance of terms/privacy).
- `/login`, `/verify-email`, `/forgot-password`, `/reset-password`, `/accept-invite`.

### Live onboarding

1. Verify email and create the first merchant/workspace.
2. Select a plan and complete Vasooli subscription checkout.
3. Create the first owner membership; invite additional users.
4. Connect an ERP and run a read-only test sync.
5. Configure sending domain/from address and verify it.
6. Connect the merchant’s Razorpay account (OAuth preferred), complete a test payment-link flow, and verify webhooks.
7. Set reminder policy, suppression/consent defaults, and timezone.
8. Show a readiness checklist. Live sending stays paused until all mandatory checks pass and the owner explicitly enables automation.

Onboarding is resumable and idempotent. A failed step has a remediation path and never creates a second merchant, subscription, connector, or webhook registration.

### Live operating screens

`/dashboard`, `/invoices`, `/customers`, `/reminders`, `/exceptions`, `/integrations`, `/billing`, `/team`, `/settings`, and `/audit-log`. The layout shows a persistent **DEMO** or **LIVE** badge and the merchant name. All actions are capability-driven (`can("invoice.write")`, etc.), not hard-coded role checks.

## 5. IAM and RBAC design

### Identity

Create a verified email-based `users` table with password hash (Argon2id), status (`pending`, `active`, `suspended`, `deleted`), timestamps, last-login metadata, password-change timestamp, and optional MFA metadata. Store short-lived access tokens in memory and use rotating, hashed, HttpOnly refresh tokens with reuse detection and revocation. Add email verification, password reset, session listing/revocation, login throttling, and audit events.

Do not use the current reviewer one-click flow for live authentication. Preserve it behind the demo boundary only.

### Tenant membership and roles

Add:

- `merchants` (legal/business profile, timezone, mode/status, plan/entitlement reference, onboarding state).
- `merchant_memberships` (`user_id`, `merchant_id`, role, active, joined/revoked timestamps, invitation source).
- `roles` (merchant-scoped custom roles plus immutable system roles).
- `permissions` (stable codenames and descriptions).
- `role_permissions`, `user_permission_overrides` (deny-by-default; avoid overrides until an explicit use case exists).
- `merchant_invitations` (single-use, hashed token, expiry, intended role, inviter, accepted/revoked timestamps).
- `sessions`, `mfa_factors`, `auth_events`, and `audit_events`.

Recommended initial system roles:

| Role | Intended access |
|---|---|
| Owner | Merchant settings, billing, integrations, team, all operational actions |
| Admin | Team and operations; no subscription ownership transfer or destructive billing action by default |
| Collector | Invoices, customers, reminders, payment links, promises and disputes |
| Analyst | Read-only invoices, recovery metrics, exports |
| Billing manager | Subscription and invoices for Vasooli; no ERP credential access |
| Support (platform) | Time-limited, audited support access with merchant consent/break-glass controls |

Permission families should include `merchant.read/write`, `team.read/invite/manage`, `invoice.read/write/import`, `customer.read/write`, `reminder.read/send/pause/configure`, `payment_link.create/read/refund`, `erp.read/sync/configure`, `billing.read/manage`, `audit.read/export`, and `support.break_glass`.

### Authorization implementation

- Resolve the active merchant from the session plus an explicit path/header context; never infer it from “first merchant.”
- `require_membership(permission)` checks active membership, permission, merchant status, and entitlement where relevant.
- `get_scoped_object()` loads by both `merchant_id` and object ID, returning not-found for another merchant to avoid IDOR leaks.
- Put `merchant_id` on invoices, customers, reminders, policies, connectors, payment records, exports, jobs, and all new tenant-owned tables.
- Use PostgreSQL RLS with a transaction-local `app.merchant_id`; add tests for every endpoint that previously queried globally.
- Require re-authentication/MFA for credential rotation, billing ownership changes, refunds/write-offs, exports, and break-glass access.
- Enforce separation of duties for subscription cancellation/refund, payment-account changes, write-offs, and automation activation where the organization chooses it; record the second approver.

### The invoice number is currently a global identity key

Adding `merchant_id` columns is necessary but **not sufficient**. The invoice number is used as a globally unique identifier in three places, and every one of them breaks the moment two merchants both have an `INV-001` — which is the common case, not an edge case.

| Location | Current behaviour | Failure with two merchants |
|---|---|---|
| `models/invoice.py` | `invoice_number` is `unique=True` across the whole table | The second merchant's import fails on an integrity error |
| `services/provisioning.py` | `reference_id_for()` returns `vsl-{invoice_number}`, and `PaymentLink.reference_id` is `unique=True` | The second merchant cannot create a payment link, even though their own Razorpay account would accept it |
| `services/messaging.py` | `reply_address_for()` mints `invoice-{invoice_number}@domain` | **Both merchants get the same reply address** |

The third is the dangerous one, because it fails silently rather than loudly. `_find_inbound_invoice()` in `api/webhooks.py` correlates an inbound reply on sender address plus reply alias, over an **unscoped** `select(Invoice)`. In B2B the same buyer email frequently appears under several suppliers, so a customer who owes both merchants and replies about `INV-001` can have that reply — and the promise or dispute extracted from it — attached to the wrong merchant's invoice. That is a cross-tenant data leak and an incorrect recovery action from a single message.

Required in Phase 1, alongside the column work:

- Replace the global unique on `invoice_number` with `UniqueConstraint("merchant_id", "invoice_number")`.
- Derive `reference_id` from something tenant-unique — the invoice UUID, or a merchant prefix — while keeping it stable per invoice, since stability is what makes a retry after a Razorpay timeout idempotent rather than duplicating a link.
- Mint the reply alias from a tenant-unique, non-guessable token rather than the invoice number. An address derived from a sequential invoice number is also trivially enumerable by an outsider, which the current single-merchant deployment gets away with and a real one should not.
- Scope `_find_inbound_invoice()` by merchant, resolved from the receiving alias before any invoice lookup.

**Interaction with the demo freeze (§0):** changing the alias scheme changes an externally visible address. Keep the legacy `invoice-{number}@` format working for the demo merchant — accept both formats on the inbound path, mint the new one only for live merchants — or treat it as a deliberate, reviewed re-baseline of the demo fixtures. Do not let it change silently.

## 6. Pricing and subscription billing

Keep the agreed working pricing:

| Plan | Base price | Included active invoices | Positioning |
|---|---:|---:|---|
| Starter | ₹1,999/month | Up to 100 | Small merchant / first workflow |
| Growth | ₹5,999/month | Up to 500 | Growing collections team |
| Scale | ₹14,999/month | Up to 2,000 | Larger volume and priority support |

“Active invoices” means invoices currently under Vasooli management (unpaid/part-paid and within the merchant’s retention window); define the exact counting rule in the terms and UI. Show tax treatment, overage behavior, invoice retention, support level, and cancellation/refund policy before checkout. Do not silently downgrade or delete data when a limit is crossed.

Create immutable, versioned plan records in the database mapped to Razorpay `plan_id` values. Do not use price text in frontend code as an entitlement. Add:

- `billing_customers` (Vasooli Razorpay customer reference, legal/billing details).
- `billing_subscriptions` (merchant, plan version, Razorpay subscription ID, status, quantity, period, trial/grace, cancel-at-period-end, timestamps).
- `billing_events` (provider event ID unique, signature result, payload hash/reference, received/processed timestamps, outcome).
- `billing_entitlements` (effective caps/features with source and validity interval).
- `billing_invoices`, `billing_payment_attempts`, `billing_refunds`, and a reconciliation ledger.

Flow:

1. Server creates a Razorpay subscription for the selected immutable plan; client opens checkout using the returned short-lived data.
2. Server verifies the checkout signature and waits for the signed webhook as source of truth.
3. A state machine handles `created`, `authenticated`, `active`, `past_due`, `paused`, `cancelled`, and `expired`, including grace-period and suspension rules.
4. The entitlement middleware gates live sending, connector sync, active-invoice imports, and user seats according to the verified state.
5. Daily reconciliation compares Razorpay subscriptions/payments with the local ledger and alerts on drift.

Use Razorpay’s Subscriptions API and subscription webhooks. Razorpay plans are effectively immutable after creation, so a price change creates a new plan version and a controlled migration path.

## 7. Merchant Razorpay account and customer collections

There are two distinct money flows:

1. **Vasooli subscription:** merchant pays Vasooli through the Vasooli Razorpay account.
2. **Customer collection:** merchant’s customer pays the merchant’s own Razorpay account through a payment link/order generated by Vasooli.

Never use the first flow’s keys or webhooks for the second.

### Preferred connection

Use Razorpay Technology Partner OAuth so a merchant authorizes Vasooli without handing over an API secret. Store the encrypted access/refresh tokens, Razorpay account/merchant ID, scopes, token expiry, connection status, and last successful verification in a per-merchant `razorpay_connections` table. Refresh tokens server-side, rotate encryption keys, and redact credentials from logs/UI. Partner OAuth approval and production onboarding must be completed before promising this flow.

### Fallback connection

If OAuth is unavailable for the target account type, offer an explicitly labeled BYO-key path. Keys are entered only over TLS, encrypted with KMS-managed keys, never returned after save, access-controlled separately, and tested with a least-privilege API call. Make this a temporary compatibility path with a removal date.

### Collection flow

- Create a payment link/order in the merchant account with an idempotency key containing merchant + invoice + attempt.
- Persist provider IDs and a signed redirect/webhook status; never mark an invoice paid from a browser redirect alone.
- Verify webhook signatures, route by merchant Razorpay account ID, deduplicate event IDs, and handle out-of-order events.
- Reconcile payment, refund, and settlement status daily; keep an append-only payment ledger.
- Stop reminders immediately when payment/settlement or a valid promise/dispute is recorded.
- Provide the merchant’s own Razorpay dashboard link and settlement explanation; Vasooli does not hold customer funds.

## 8. ERP integration layer

Define a canonical internal model (`CanonicalInvoice`, `CanonicalCustomer`, `CanonicalPayment`, `CanonicalCreditNote`) independent of any ERP. Every adapter maps into it and preserves `source_system`, `source_tenant`, `source_id`, `source_version/updated_at`, currency, tax, due date, contact/consent fields, and raw-payload reference.

### Connection and sync tables

Add `erp_connections`, `erp_credentials`, `erp_sync_cursors`, `erp_sync_runs`, `erp_records`, `erp_mappings`, `erp_webhook_events`, and `integration_failures` (dead-letter + retry metadata). Unique keys must include merchant, provider, source tenant, and source record ID. Encrypt credentials and keep raw payloads in restricted storage with retention controls.

### Adapters

- **Zoho Books:** OAuth authorization, organization ID selection, invoice import using incremental `last_modified_time`, webhook registration, periodic reconciliation, token refresh, and rate-limit backoff.
- **Tally:** Tally is commonly local/on-premise and exposes HTTP/XML while a company is loaded. Ship a signed outbound edge agent or connector gateway that makes outbound TLS calls to Vasooli; do not expose a merchant’s port 9000 to the internet. Agent identity, version, heartbeat, queueing, and revocation are required.
- **Custom ERP:** signed webhook endpoint plus REST polling and/or SFTP CSV adapter. Provide a versioned mapping UI, sample payload validator, field-level error report, and replay controls.

Connector contract requirements: read-only first sync, dry-run preview, cursor-based incremental sync, idempotent upsert, tombstone/cancellation handling, explicit timezone/currency normalization, backoff, dead-letter queue, per-merchant rate limits, health/freshness status, and a manual “sync now” permission.

Use webhook-first updates where available and polling as a backstop. A stale or failed source must be visible and, by policy, must block sending rather than cause guesses.

## 9. Reminder policy and recovery engine

Replace the global constants and single-merchant assumptions with versioned merchant policies.

### Correction: 3/7/14 is incompatible with the current cooldown floor

An earlier draft set the default schedule to 3/7/14 while also keeping "global platform safety floors". Those two requirements collide, and the collision is silent. Verified against the real `evaluate_reminder()`:

```text
--- current 3/10/21 ---
  tier 2 @ day 10 (gap 7d): cooldown OK  -> send
  tier 3 @ day 21 (gap 11d): cooldown OK -> send

--- proposed 3/7/14 ---
  tier 2 @ day  7 (gap 4d): cooldown FAIL -> hold
```

`MIN_COOLDOWN_DAYS = 7`, so a tier-2 reminder on day 7 sits only 4 days after the day-3 send. The `cooldown_respected` check fails and the reminder is **held with no user-visible explanation** — a merchant configures 3/7/14 and silently receives roughly 3/10/17.

The existing 3/10/21 was not arbitrary: its gaps are exactly 7 and 11 days. It was built to clear its own floor.

### Resolution

Split the single constant into a merchant-editable value and a platform floor:

- `DEFAULT_COOLDOWN_DAYS = 7` — the default, and the value the demo keeps forever (§0).
- `PLATFORM_MIN_COOLDOWN_DAYS = 3` — a hard floor no merchant policy may go below, not editable in the UI or the API.
- **Default schedule stays 3, 10, 21 with cooldown 7.** This is today's behaviour, so the demo is untouched and no existing invoice changes tier timing at migration.
- **3/7/14 is a supported merchant choice**, selected with cooldown 4. It is a first-class preset in the policy editor, not a special case.

### Validation at save time, not at send time

A cadence that cannot fire is a bug the merchant should learn about while editing, not three days later when a reminder silently holds. Reject a policy unless:

- tier offsets are strictly increasing;
- **every consecutive gap is greater than or equal to the policy's cooldown**;
- cooldown is greater than or equal to `PLATFORM_MIN_COOLDOWN_DAYS`;
- attempts are within the platform cap;
- the error names the offending pair, e.g. "Day 7 is only 4 days after day 3, but your cooldown is 7 days."

Show the effective cooldown beside the schedule in the editor so the interaction is visible before saving.

### Remaining policy requirements

- Clarify in the UI whether values are absolute offsets or intervals; store absolute offsets internally.
- Per-policy timezone, sending window, max attempts, cooldown, channel, template, escalation, and pause conditions.
- Per-invoice/per-customer suppression, opt-out, bounce, dispute, promise, and manual pause.
- Global platform safety floors, daily/provider rate limits, quiet hours, and emergency kill switch.

`reminder_policy_versions` are immutable once used. A new version applies only to newly scheduled work unless the merchant explicitly reschedules. Do not create catch-up bursts after downtime; apply a bounded recovery rule and show skipped work.

The existing deterministic policy engine remains the source of truth. AI may draft/explain copy, but cannot choose recipients, timing, amounts, payment state, or policy exceptions. Every decision stores policy version, source invoice version, actor/system identity, and reason.

## 10. Email delivery and customer safety

- Verify a merchant sending domain (SPF/DKIM/DMARC) or use a clearly branded platform sender with reply routing.
- Keep provider credentials in secret storage; isolate demo and live provider accounts/domains.
- Use the durable outbox with leases, retry/backoff, idempotency key, provider message ID, and dead-letter state already present in Vasooli.
- Maintain suppression lists for unsubscribe, hard bounce, abuse complaint, legal hold, and merchant block.
- Include invoice identity, amount, due date, merchant identity, payment link, support contact, and a safe opt-out path; never include secrets or excessive customer data.
- Enforce per-merchant and global quotas, bounce monitoring, provider reputation alerts, and a send preview/test recipient during onboarding.
- Log rendered-template hash and policy decision, not unnecessary message body/customer PII.

## 11. Backend/API implementation map

Implement incrementally under the existing `backend/app` structure:

| Area | New or changed modules |
|---|---|
| Auth/IAM | `models/user.py`, `models/iam.py`, `api/auth.py`, `api/team.py`, `services/auth.py`, `services/authorization.py`, `api/deps.py` |
| Tenancy | `models/merchant.py`, `services/tenant_context.py`, repository filters, PostgreSQL RLS migration |
| Billing | `models/billing.py`, `api/billing.py`, `services/billing.py`, verified Razorpay webhook handler and reconciliation job |
| Collections | `models/payment_connection.py`, `api/payment_connections.py`, Razorpay OAuth/token service, payment-link and ledger services |
| Integrations | `integrations/base.py`, `integrations/zoho.py`, `integrations/tally_agent.py`, `integrations/custom.py`, sync/webhook APIs and workers |
| Recovery | merchant-aware invoice/customer queries, policy versions, scheduling/outbox changes, suppression service |
| Audit | append-only audit event schema, actor/request/merchant/source metadata, export endpoint |
| Workers | separate scheduler, sync, recovery, delivery, billing/reconciliation processes; health/heartbeat endpoints |

Every new endpoint must declare: auth requirement, permission, merchant scope, idempotency behavior, rate limit, audit event, and error contract.

## 12. Frontend implementation map

- Add explicit `/demo` and `/live` route groups and an access chooser.
- Add registration, verification, reset, invite acceptance, pricing, checkout return, onboarding checklist, billing, team, integrations, policy editor, and readiness screens.
- Replace hard-coded “Razorpay Test mode” and “Single merchant workspace” labels with server-provided environment/mode/capability data.
- Show plan usage and projected limit impact before import or activation.
- Disable (with explanation) actions for missing permission, suspended billing, stale ERP, unverified sender, missing payment connection, or paused automation.
- Preserve the existing demo reviewer controls only inside the demo route group.
- Add error, loading, empty, retry, and “last synced/last sent” states; never imply payment or email success from an optimistic client response.

## 13. Deployment and reliability

Before production, remove two single-process assumptions, not one.

**The scheduler.** APScheduler currently runs inside the API process.

**Module-level runtime state.** `app/core/runtime.py` caches the email-redirect override in module state, mirrored to `demo_settings` and rehydrated at startup. The file already documents itself as single-process-only. This makes the API **not stateless**: with more than one replica, the replica that serves a settings write and the replica that later sends mail hold different values, and the reviewer flow breaks in a way that looks random. Move these reads to the database — behind a short-TTL cache if the query cost matters — before adding a second replica. This applies to API replicas, not only to workers.

Run independently deployable processes:

- API replicas (stateless).
- One scheduler with a lease/leader lock.
- Connector/sync workers.
- Recovery/policy workers.
- Email delivery workers.
- Billing/reconciliation workers.

Use PostgreSQL `SKIP LOCKED` queues initially (or a managed queue when volume requires it). Each job has a lease, attempt count, next-run time, idempotency key, and dead-letter path. Keep internal Postgres only; terminate TLS at a managed edge/Nginx; use immutable container/image revisions.

Separate staging and production projects, domains, databases, storage, queues, secrets, email domains, Razorpay accounts/modes, and ERP test credentials. Production secrets come from a secret manager, not `.env` committed files. Define SLOs for API availability/latency, sync freshness, queue age, email acceptance, webhook processing, and billing reconciliation.

Backups are encrypted off-host with independent key custody. Define RPO/RTO, alert on failed backups, and perform an observed restore drill before launch and at least quarterly.

## 14. Phased delivery and acceptance criteria

### Phase 0 – baseline and safety (1 week)

- **First task, before any other commit:** tag the current demo release and capture the §0 golden fixtures — API response snapshots, seeded-ledger dump, policy-decision traces for all eight invoices, and screenshots of every demo screen. Nothing else in this plan may start until this is done, because after the first migration the baseline is no longer recoverable.
- Wire the demo regression suite as a **required, merge-blocking CI gate**, in the same class as the existing architecture tests.
- Inventory all global queries, demo flags, hard-coded cadence values, and provider credentials. The answer for queries is already known: only `services/ingestion.py` filters by `merchant_id` today — roughly forty other call sites read their tables globally. Treat that inventory as a checklist, not a discovery exercise.
- **Submit the Razorpay Technology Partner OAuth application now.** It is an external approval on Razorpay's timeline, not yours, and Phase 3 cannot start without it. Filing it in Phase 3 puts an uncontrolled dependency on the critical path; filing it here lets it run in parallel with Phases 1 and 2. If approval is refused or delayed, the documented BYO-key fallback (§7) becomes the launch path — decide that early, not under deadline.
- Add CI gates, dependency/security scanning, error tracking, and a production-like staging environment.
- **Exit:** golden fixtures committed and demo regression suite green as a required check; Partner OAuth application submitted with a tracking reference; rollback procedure rehearsed.

### Phase 1 – tenancy and live identity (2–3 weeks)

- Add merchant-aware schema, RLS, user lifecycle, sessions, verification/reset, memberships, roles, permissions, invites, audit events.
- Backfill current records into an explicit demo merchant and verify no cross-tenant reads.
- Re-scope the three identity keys that are currently global — `invoice_number`, payment-link `reference_id`, and the inbound reply alias — per §5. Column work alone does not make the system multi-tenant.
- **Exit:** IDOR/cross-tenant tests pass; two merchants can both hold an `INV-001` and each can import it, provision a payment link for it, and receive a reply about it with no crossover; demo login/reviewer flow unchanged against the Phase 0 fixtures; migration is reversible/expand-contract.

### Phase 2 – pricing, billing, and entitlements (2 weeks)

- Publish the three plans; create immutable Razorpay plan versions; implement checkout, signed webhooks, state machine, grace/suspension, invoice/usage display, and reconciliation.
- **Exit:** sandbox card/webhook replay tests pass; duplicate/out-of-order events are harmless; suspended merchants cannot send/import beyond policy.

### Phase 3 – merchant Razorpay collections (2–3 weeks)

- Complete Technology Partner OAuth approval; implement per-merchant connection storage, payment links/orders, signed webhook routing, refunds/settlements ledger, and fallback BYO-key controls if required.
- **Exit:** test merchant receives a test payment; Vasooli subscription and merchant collection ledgers remain separate; payment stops reminders.

### Phase 4 – ERP adapter platform (3–4 weeks)

- Canonical DTOs, connection state, cursor/incremental sync, dead-letter/replay, freshness, and audit framework.
- Deliver Zoho first, Tally edge agent second, custom adapter contract third.
- **Exit:** fixture and sandbox tests for each connector; replay and partial failure tests; no duplicate invoices/customers.

### Phase 5 – real recovery and merchant controls (2 weeks)

- Versioned editable cadence (default 3/10/21 with cooldown 7, unchanged from today; 3/7/14 with cooldown 4 offered as a preset), save-time policy validation, platform safety floors, suppression/consent, sending-domain verification, live templates, quotas, and pause/kill switch.
- **Exit:** controlled pilot sends only to allow-listed merchants/customers; every send has an auditable decision and can be cancelled before dispatch.

### Phase 6 – operations, security, and pilot (2–3 weeks)

- Worker separation, autoscaling/health, dashboards/alerts, backup restore, incident runbook, support/export/deletion workflows, legal pages, and independent security review.
- **Exit:** launch gates below are signed by named owners; 1–3 paid design partners complete a live cycle.

The calendar is approximately 14–18 weeks for a small experienced team; a solo implementation should be planned as a longer staged delivery. Dates are estimates, not permission to skip gates.

## 15. Migration and no-breakage strategy

1. Use expand/contract Alembic migrations: add nullable columns/tables, dual-read/write, backfill in batches, verify, then enforce constraints and remove legacy paths in a later release.
2. Take and verify a backup before every production migration; rehearse against a production-like copy.
3. Keep the current demo data and credentials behind an explicit demo merchant/mode. Never convert it into a real merchant.
4. Introduce merchant context in repositories first, then switch endpoints one group at a time; fail closed if context is missing.
5. Feature-flag live registration, sending, ERP writes, and payment links independently. Roll back by disabling the flag, not by deleting rows.
6. Maintain contract tests for existing webhook signatures, outbox leases/retries, recovery decisions, promises/disputes, reconciliation, and reviewer mode.
7. Use canary releases and observe queue age, send rate, webhook failures, sync freshness, 401/403 rates, and database errors before broad rollout.

## 16. Test and verification matrix

### Automated

- Auth lifecycle: registration, verification, reset, refresh rotation/reuse, logout-all, throttling, MFA, invite expiry.
- IAM: every permission, custom role, revoke/leave, support break-glass expiry, separation-of-duties.
- Tenancy: cross-merchant object IDs, global search/export, background jobs, webhook routing, RLS bypass attempts.
- Duplicate invoice numbers across merchants: two merchants each holding `INV-001` must both import, both provision a payment link, and both receive an inbound reply correctly. Include the case where the *same customer email* owes both merchants — the reply must reach the right invoice, and the wrong merchant's row must be untouched.
- Billing: plan caps, checkout signature, webhook replay/out-of-order, cancellation/grace/suspension, reconciliation, refunds.
- Razorpay collections: OAuth refresh, encrypted credential access, idempotent links, payment/refund/settlement events, reminder stop.
- ERP: full/incremental sync, cursor recovery, rate limits, duplicate/tombstone/conflict handling, malformed payloads, stale source.
- Recovery/email: default 3/10/21 scheduling, the 3/7/14 preset, custom policies, quiet hours, suppression, bounce, provider failure, retries, cancellation, kill switch.
- Policy validation: a cadence whose gap is shorter than its cooldown is rejected at save time with a message naming the offending pair; a cooldown below `PLATFORM_MIN_COOLDOWN_DAYS` is rejected; no saved policy can produce a silently-held reminder.
- Demo freeze (§0): golden-fixture comparison for every demo endpoint, the seeded ledger, and the eight recorded policy traces — a required, merge-blocking check.
- Migration upgrade/downgrade and rollback on a production-like database.
- Frontend: Demo/Live isolation, capability-based controls, onboarding interruption/resume, error states, accessibility and mobile layouts.

### Operational verification

- Load test imports, policy evaluation, queue workers, webhooks, and concurrent merchant activity.
- Chaos test worker crash, duplicate jobs, database failover, provider outage, clock skew, and delayed webhooks.
- Restore encrypted backups and measure observed RPO/RTO.
- Run an independent penetration test; internal automated tests do not count as that review.

## 17. Security, legal, and support launch gates

Named owners must attach evidence and review dates for each gate:

- Production secrets, TLS, domain verification, rotation, and environment separation.
- Tenant isolation/RLS and authorization review.
- Independent penetration test with critical/high findings resolved or risk-accepted.
- Backup restore drill with measured RPO/RTO.
- Immutable CI release, migration rehearsal, rollback, and dependency vulnerability SLA.
- Privacy policy, terms, DPA/subprocessors, data-hosting region, retention/deletion/export, customer consent/opt-out, and breach notification process.
- Support channels/hours/targets, onboarding/offboarding, verified deletion, and escalation ownership.
- Incident commander, severity definitions, status communication, evidence preservation, session revocation, and credential rotation procedures.
- Pricing/tax/refund/cancellation terms, merchant agreement, and product liability review.

Launch is **blocked** if any gate lacks an owner/evidence, if live and demo providers are not separated, if a merchant cannot revoke sending/payment access, if a restore drill and cross-tenant test have not passed, or if **the demo regression suite is not green against the Phase 0 golden fixtures** (§0).

## 18. Definition of done

Vasooli is production-ready when a new merchant can register, pay for a plan, invite a least-privilege team, connect an ERP, pass a read-only sync, connect its own Razorpay account, configure 3/7/14 or another policy that passes save-time validation, verify email sending, run a real payment-link cycle, and recover an invoice without operator simulation. The same release must leave the demo **bit-for-bit unchanged in behaviour against the Phase 0 golden fixtures** (§0), pass the full regression/security/restore suite, provide auditable controls and support processes, and have signed launch evidence.

## 19. Decision log

- **Demo behaviour is frozen (§0)** and enforced by a merge-blocking regression suite against fixtures captured before any production commit. Production work is additive to the demo, never a modification of it.
- Keep Demo and Live as separate **modes** sharing one database, isolated by RLS and an explicit demo merchant — not separate data planes. Provider credentials, webhooks, cookies, and queues stay separated as they are today. This supersedes the earlier "separate database/schema" line, which contradicted both the demo freeze and Phase 1's backfill step.
- Keep the agreed Starter/Growth/Scale pricing above; plan IDs and entitlements are server-side and versioned.
- Use Razorpay for both subscription billing and merchant customer collections, but through separate accounts, credentials, webhooks, and ledgers.
- Prefer Razorpay Technology Partner OAuth for merchant account setup; retain encrypted BYO credentials only as a documented fallback.
- Default reminder offsets stay **3, 10, 21 with a 7-day cooldown** — today's values, so the demo is untouched and no live invoice shifts tier timing at migration. **3/7/14 with a 4-day cooldown ships as a merchant-selectable preset.** `MIN_COOLDOWN_DAYS` splits into an editable `DEFAULT_COOLDOWN_DAYS = 7` and a hard `PLATFORM_MIN_COOLDOWN_DAYS = 3`. This supersedes the earlier "default 3/7/14" decision, which was verified to produce silently-held reminders against the existing cooldown check (§9).
- Cadence policies are validated at save time, not discovered at send time: every consecutive gap must be at least the policy's cooldown.
- The Razorpay Technology Partner OAuth application is submitted in Phase 0, not Phase 3, because approval runs on Razorpay's timeline and otherwise blocks the critical path.
- The invoice number stops being a global identity key (§5). Uniqueness becomes `(merchant_id, invoice_number)`, and the payment-link reference and inbound reply alias are re-derived from tenant-unique values rather than the invoice number.
- Use Zoho OAuth/webhooks, a Tally outbound edge agent, and a signed custom-ERP contract.
- Port IAM/RBAC concepts from the reference project; do not port its Django/pharmacy/clinical implementation.
- Preserve deterministic policy/recovery and durable outbox behavior; AI remains assistive and cannot make financial or contact decisions.

## 20. Primary external integration references

- [Razorpay Technology Partner OAuth](https://razorpay.com/docs/partners/technology-partners/onboard-businesses/integrate-oauth/)
- [Razorpay subscription plans](https://razorpay.com/docs/payments/subscriptions/create-plans/) and [subscription webhooks](https://razorpay.com/docs/payments/subscriptions/subscribe-to-webhooks/)
- [Razorpay Payment Links API](https://razorpay.com/docs/api/payments/payment-links/)
- [Zoho Books OAuth](https://www.zoho.com/books/api/v3/oauth/), [invoices](https://www.zoho.com/books/api/v3/invoices/), and [webhooks](https://www.zoho.com/books/api/v3/webhooks/)
- [Tally XML integration](https://help.tallysolutions.com/xml-integration/)

