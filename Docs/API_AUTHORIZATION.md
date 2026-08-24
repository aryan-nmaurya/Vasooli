# API authorization

Generated from the live OpenAPI schema and **verified empirically** — every endpoint
below was called without a credential and its response recorded. The check lives in
`backend/tests/integration/test_auth.py`, so a new unprotected endpoint fails the build
rather than waiting to be noticed.

## The model

One operator role. Vasooli runs for a single merchant; per-user accounts and an IAM
model would be scaffolding around a system with exactly one user, and scaffolding
nobody needs is where security bugs hide.

Two ways to prove you are that operator:

| Credential | Used by | Lifetime |
|---|---|---|
| `X-Admin-Key` header | scripts, scheduler, the dashboard's server-side proxy | long-lived, never in a browser |
| `vasooli_session` cookie | a browser that has logged in | 12 hours, httpOnly, SameSite=Lax |

**There is no resource-ownership check, and there should not be.** Every invoice belongs
to the one merchant; an ownership check over a single-tenant dataset would be a
comparison that always passes, which is worse than none because it looks like
protection.

## What is deliberately NOT gated

| Endpoint | Why | What protects it instead |
|---|---|---|
| `/health`, `/live`, `/ready` | Deployment platforms probe before any credential exists | Exposes no data |
| `/api/webhooks/razorpay` | Razorpay cannot log in | HMAC-SHA256 over the raw body, plus a unique event id |
| `/api/auth/login` | It is how you get a credential | 10 attempts/min per client |

**CORS is not authorization.** The origin allowlist is a browser convenience; a
non-browser client ignores it entirely. Every endpoint is gated independently.

## Full table

| Endpoint | R/W | Auth | Sensitive data | Risk if unprotected |
|---|---|---|---|---|
| `POST /api/admin/run-cycle` | W | **session or admin key** | — | Unauthorised state change or outbound contact. |
| `POST /api/auth/login` | W | public, rate limited | — | Brute force. 10 attempts/min per client. |
| `POST /api/auth/logout` | W | public, rate limited | — | Brute force. 10 attempts/min per client. |
| `GET /api/dashboard/audit` | R | **session or admin key** | everything, in one list | Customer PII disclosure. |
| `GET /api/dashboard/exceptions` | R | **session or admin key** | payment + customer data | Customer PII disclosure. |
| `POST /api/dashboard/exceptions/events/{provider_event_id}/retry` | W | **session or admin key** | — | Unauthorised state change or outbound contact. |
| `POST /api/dashboard/exceptions/reminders/{reminder_id}/retry` | W | **session or admin key** | — | Unauthorised state change or outbound contact. |
| `GET /api/dashboard/invoices/{invoice_id}` | R | **session or admin key** | customer email, full history | Customer PII disclosure. |
| `POST /api/dashboard/invoices/{invoice_id}/escalate` | W | **session or admin key** | — | Unauthorised state change or outbound contact. |
| `POST /api/dashboard/invoices/{invoice_id}/write-off` | W | **session or admin key** | — | Unauthorised state change or outbound contact. |
| `GET /api/dashboard/overview` | R | **session or admin key** | aggregate money figures | Customer PII disclosure. |
| `GET /api/dashboard/promises` | R | **session or admin key** | customer names, quoted replies | Customer PII disclosure. |
| `GET /api/dashboard/queue` | R | **session or admin key** | customer names, amounts owed | Customer PII disclosure. |
| `GET /api/invoices` | R | **session or admin key** | customer names, amounts | Customer PII disclosure. |
| `POST /api/invoices/batch` | W | **session or admin key** | — | Unauthorised state change or outbound contact. |
| `POST /api/invoices/provision-batch` | W | **session or admin key** | — | Unauthorised state change or outbound contact. |
| `GET /api/invoices/{invoice_id}` | R | **session or admin key** | customer names, amounts | Customer PII disclosure. |
| `POST /api/invoices/{invoice_id}/provision` | W | **session or admin key** | — | Unauthorised state change or outbound contact. |
| `POST /api/invoices/{invoice_id}/simulate-reply` | W | **session or admin key** | — | Unauthorised state change or outbound contact. |
| `POST /api/webhooks/razorpay` | W | HMAC signature | — | Forged payment. HMAC over the raw body + unique event id. |
| `GET /health` | R | public | — | None — no data. Probed before any credential exists. |
