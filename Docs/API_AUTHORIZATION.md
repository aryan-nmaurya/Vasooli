# API authorization

Generated from the live OpenAPI schema and **verified empirically** — every endpoint
below was called without a credential and its response recorded. The check lives in
`backend/tests/integration/test_auth.py`, so a new unprotected endpoint fails the build
rather than waiting to be noticed.

## The model

Every human has a named database account. Accounts have independent password hashes,
lockouts, active/disabled state, session generations, and one of three roles:
`admin`, `operator`, or read-only `auditor`. Password reset and disable both revoke
that account's existing sessions immediately.

Two credential types are accepted:

| Credential | Used by | Lifetime |
|---|---|---|
| `X-Admin-Key` header | service scripts and deployment smoke checks | long-lived, never configured in the frontend |
| `vasooli_session` cookie | a named human who has logged in | 12 hours, httpOnly, SameSite=Lax, independently revocable |

**This document describes the single-tenant operator console only** (`/api/dashboard`,
`/api/invoices`, `/api/demo`), which is reached with the credentials above. The
multi-tenant live routes under `/api/live/*` are a different surface with a different
model — merchant session, role and permission, plus per-request tenant scoping — and
nothing in this file describes them.

**Within the operator console there is no resource-ownership check, and there should
not be.** Its dataset is the seeded demo ledger belonging to one merchant; an ownership
check over a single-tenant dataset is a comparison that always passes, which is worse
than none because it looks like protection.

**Do not carry that sentence over to a live route.** There, ownership is checked twice:
in the application, where every route taking a child id re-loads the parent through
`get_scoped_object(..., context.merchant.id)`; and in the database, where row-level
security is forced on every merchant-owned table and on the eight tables that hang off
`invoices` by `invoice_id`. The application check is the one that produces a good error
message. The database check is the one that still holds when somebody forgets the first.

## What is deliberately NOT gated

| Endpoint | Why | What protects it instead |
|---|---|---|
| `/health`, `/live` | Deployment platforms probe before any credential exists | Exposes no customer data |
| `/api/webhooks/razorpay` | Razorpay cannot log in | HMAC-SHA256 over the raw body, plus a unique event id |
| `/api/webhooks/resend/inbound` | Resend cannot log in | Svix signature over raw body, event-id dedup, authenticated body retrieval, sender/thread correlation |
| `/api/webhooks/inbound-email` | A trusted custom normalizer cannot log in | HMAC-SHA256 over raw body, event-id dedup, sender/thread correlation |
| `/api/auth/login` | It is how you get a credential | edge rate limit plus 5 failures/account → 15-minute lock |

**CORS is not authorization.** The origin allowlist is a browser convenience; a
non-browser client ignores it entirely. Every endpoint is gated independently.

## Route classes

The dynamic integration test is the authoritative inventory; this grouped summary is
kept intentionally smaller so it cannot pretend to be exhaustive while going stale.

| Route class | Reads | Writes | Protection |
|---|---|---|---|
| `/api/dashboard/**` | named session or service key | admin/operator session or service key; auditor rejected | active account and session generation checked on every request |
| `/api/invoices/**` and `/api/admin/**` | named session or service key | admin/operator session or service key; auditor rejected | same central dependency; object lookup never bypasses it |
| `/api/invoices/{id}/simulate-reply` | — | admin/operator session or service key | explicitly labelled demo control |
| `/api/export/**` | named session or service key; auditors included, since exporting evidence is the auditor's job | — | whole-router dependency; the frontend proxy allowlists the path and forwards only `format`, `status`, `reason` |
| `/api/invoices/import` | named session or service key (template download) | admin/operator session or service key; auditor rejected | `dry_run` defaults to true, so only an explicit `dry_run=false` writes; 5 MB and 5,000-row ceilings |
| `/api/dashboard/invoices/{id}/payments`, `/api/dashboard/payments/{id}/reverse` | named session or service key | admin/operator session or service key; auditor rejected | records money on an operator's word rather than a provider's signature, so every row carries `recorded_by` and the audit detail says `"verification": "operator_asserted"` |
| `/api/dashboard/exceptions/events/{id}/match` | — | admin/operator session or service key; auditor rejected | the operator chooses the invoice; the amount is read from the stored webhook payload, never from the request |
| `/api/payments/methods` | named session or service key | — | the method list the form may offer, served from the service that would reject anything else |
| `/api/webhooks/**` | — | provider signature only | raw-body verification plus provider event deduplication and correlation |
| `/api/auth/login`, `/api/auth/logout` | public | public | generic failures, rate limits, account lockout, httpOnly session cookie |
| `/api/auth/modes` | public | — | says only whether the reviewer button should render; the login page is unauthenticated by definition and cannot ask a gated endpoint |
| `/api/auth/reviewer` | — | public, and 404s unless `REVIEWER_ACCESS_ENABLED` | issues a session **only** for an account whose role is `auditor`, so a mistyped `REVIEWER_USERNAME` fails closed; read-only is then the same `require_operator` check that refuses every non-GET |
| `/health`, `/live` | public | — | operational status only; no customer records |

`backend/tests/integration/test_auth.py` discovers the live OpenAPI schema on every
run. Every non-public read must return 401 anonymously, every non-public mutation must
return 401 anonymously, auditors must be read-only, and a disabled or generation-
rotated account must lose access immediately.
