# Vasooli

**AI-assisted B2B receivables recovery.** Chase. Track. Reconcile. Recover.

---

## 1. The problem

Indian SMEs write off unpaid invoices not because customers refuse to pay, but because
nobody has time to chase them properly. Chasing well means remembering who promised
what, stopping when someone disputes an invoice, not nagging a customer who already
paid, and knowing the moment money actually arrives. Done by hand, it is tedious and
error-prone. Done by a naive script, it is worse: it nags everyone forever and
damages the relationships the business runs on.

## 2. The solution

Vasooli chases overdue invoices on a bounded, auditable schedule. It diagnoses *why*
each invoice is unpaid, writes a reminder in a tone that matches, stops the moment a
customer promises to pay or disputes the bill, and stops permanently the moment money
lands — confirmed by a signed webhook, not by a model's opinion.

**The measured result** (150 held-out invoices, 45 simulated days — see §9):

| | No chasing | Naive chaser | **Vasooli** |
|---|---|---|---|
| Recovery rate (by value) | 8.4% | 85.0% | **65.1%** |
| Contacts per invoice | 0 | 5.17 | **1.10** |
| Compliance breaches | 0 | **92** | **0** |

Read that honestly: a naive chaser recovers more, by contacting people five times each
and never stopping. Vasooli recovers 77% of that with **one-fifth the contacts** and
zero breaches of its own rules. The claim is not "recovers the most" — it is "recovers
most of it without behaviour you would be embarrassed to defend."

## What is real, what is simulated, what is unverified

Stated up front, because a judge should not have to work it out.

### Verified locally or in controlled test-service probes

| | Evidence |
|---|---|
| Razorpay Payment Link API | Links created against the real Razorpay test-mode API; no real funds |
| Payment Link cancellation | Verified against the live API, including the already-cancelled case |
| Webhook HMAC verification | Raw-body signature, timing-safe compare, tested |
| Webhook idempotency | Unique provider event id; 5 deliveries counted once |
| Reconciliation | Deterministic, integer paise, running-total semantics |
| Direct Razorpay sync | Recovered a Razorpay test-mode ₹9,500 payment whose webhook never arrived |
| Delivery retry, closure retry, webhook reprocessing | Bounded backoff, all tested |
| Policy engine | Pure functions, 89 tests |
| Authentication | Named DB accounts, roles, lockout/revocation, every endpoint verified unauthenticated |
| Outbound email | Durable leased outbox implemented; an earlier redirected send succeeded, but the current Resend key now returns 401 |
| Audit trail | Append-only, enforced by a database trigger |

### Simulated

| | Why |
|---|---|
| Local webhook replay | `scripts/replay_webhook.py` signs a Razorpay-shaped payload locally. Proves our handling, **not** what Razorpay sends |
| Evaluation customers | Driven by a stated behaviour model in `eval/config.py`. Measures the policy, not real human behaviour |
| Evaluation's Razorpay and email | Mocked at the integration boundary. Everything above that boundary is production code |

**Simulated customer replies are disabled.** `POST /api/invoices/{id}/simulate-reply`
injected a reply with no signature, no sender, and no correlation to an invoice
thread. It now returns 403 unless `ALLOW_SIMULATED_REPLIES=true` is set explicitly,
which production does not set, and the dashboard hides the control entirely when it is
off. Customer replies arrive as real email at
`invoice-<number>@<EMAIL_REPLY_TO_DOMAIN>` and are processed by
`POST /api/webhooks/resend/inbound`, which requires a valid Svix signature and
correlates the sender against the invoice thread before recording anything.

### Configured, not yet exercised

Deployed on Vercel (frontend) and AWS EC2 (backend + Postgres).

| | State |
|---|---|
| Live Razorpay webhook end-to-end | Endpoint deployed and reachable; delivery depends on the webhook URL being registered against a host with a valid certificate |
| Inbound email | Adapter, signature verification, threading and idempotency implemented and tested. Requires a Resend-verified domain with MX records before a reply can physically arrive |
| Outbound email | Durable leased outbox implemented and tested. Blocked on a valid `RESEND_API_KEY` |

Run `uv run python -m scripts.preflight --host https://<your-host>` for the current,
machine-checked state of every one of these. It reports what is configured, what is
missing, and the remedy for each — in the order they break.

**Nothing above is presented as real anywhere else in this repository.** If you find a
contrary claim, it is a bug.

## For reviewers

**Live:** [vasooli.space/guide](https://vasooli.space/guide) — a public page, no login
required. It states what is real, what is test-mode, and where to click first.

**Credentials** are issued per reviewer on request. Ask for an `auditor` account
rather than an operator one: the role is enforced in `app/api/deps.py`, which refuses
every non-GET request, so it cannot run a cycle, resolve a dispute, or send an email
no matter what is clicked.

If you have ten minutes and want to check the central claim rather than the interface:

| Read | What it proves |
|---|---|
| `backend/tests/architecture/test_layering.py` | The AI layer cannot import a mailer, a DB session, or the Razorpay client. Parsed from the AST, so the build fails if it is ever crossed |
| `backend/app/policy/disputes.py` | The decision to pause recovery is a pure function. No model, no clock, no database |
| `backend/tests/integration/test_disputes.py` | When recovery pauses, the audit trail attributes the *reading* to the AI and the *decision* to the policy engine — asserted, not described |
| `backend/eval/` + `backend/eval/out/results.csv` | The three-arm evaluation, including the arm where a naive chaser beats us |

```bash
cd backend && uv run pytest -q          # 691 tests
uv run python -m scripts.preflight      # every integration, live
```


## 3. Architecture

```
OVERDUE INVOICE
      ↓
  DIAGNOSIS ................ AI explains, deterministic rules decide
      ↓
  POLICY ENGINE ............ pure Python, 9 checks, no model involved
      ↓
  REMINDER ................. AI drafts, policy approves, then it sends
      ↓
  CUSTOMER REPLY ........... AI extracts, deterministic code acts
      ↓
  PROMISE / DISPUTE ........ pause escalation, or hand to a human
      ↓
  RAZORPAY PAYMENT LINK
      ↓
  SIGNED WEBHOOK ........... HMAC verified against the raw body
      ↓
  IDEMPOTENT RECONCILIATION  unique event id, running-total semantics
      ↓
  INVOICE RECOVERED
      ↓
  PAYMENT LINK CANCELLED ... recovery stops at the payment route too
      ↓
  APPEND-ONLY AUDIT TRAIL
```

**Layers**, enforced by tests that parse imports (`tests/architecture/`):

```
core  →  models  →  schemas  →  policy  →  ai  →  integrations  →  services  →  api
```

`app/ai` cannot import the database, the mailer, or the Razorpay client. That is not a
convention — a test fails the build if it ever does.

## 4. AI vs deterministic responsibilities

| Decision | Owner | Why |
|---|---|---|
| Whether money arrived | **Deterministic** | Only a signature-verified Razorpay webhook |
| How much was paid | **Deterministic** | Integer paise, running total, `max()` |
| Whether to send a reminder | **Deterministic** | `app/policy` — 9 checks, pure functions |
| Which tier / tone | **Deterministic** | Locked schedule: day 3, 10, 21 |
| Whether to stop | **Deterministic** | Cap, cooldown, promise, dispute, payment |
| Reason category | **Deterministic** | The four categories are rules over history |
| *Explaining* the reason | AI | Plain-language sentence for the dashboard |
| *Drafting* the reminder | AI | Tone and phrasing, then policy-checked |
| *Reading* a customer reply | AI | Extracts a date; the system decides what it means |

**The model can never mark an invoice paid.** Not by convention — the code that could
do it is not reachable from `app/ai`.

If both Gemini models are unavailable, Vasooli still diagnoses, still drafts (from
templates), still sends, and still recovers. The output reads more plainly. Nothing
stops.

## 5. Razorpay integration

**Payment Links.** Not Smart Collect / Virtual Accounts — Razorpay confirmed that
product is unavailable for this merchant's business type, so it is not used and not
claimed anywhere.

- One Payment Link per invoice, idempotent on `reference_id`
- `accept_partial: true` — a customer paying half is a customer paying
- Matching is deterministic and never uses the amount: `payment_link_id`, then
  `notes.invoice_id`, then `reference_id`. If none match, it goes to a human
- Webhooks: `payment_link.paid`, `payment_link.partially_paid`
- Signature: HMAC-SHA256 over the **raw request body**, compared with `compare_digest`
- On full payment the link is **cancelled**, so no second payment can arrive

**Account limits found by testing, not assumption:**
- Payment Links are capped at **₹50,000** on this account (₹50,000 works, ₹60,000 does not)
- Test mode allows roughly **6 link creations per minute**; the client paces itself

## 6. Recovery workflow

| Day overdue | Action | Tone |
|---|---|---|
| 3 | Tier 1 reminder | Polite |
| 10 | Tier 2 reminder | Firm |
| 21 | Tier 3 reminder **+ handover to a human** | Final |

At most **3 automated contacts, ever**. At least **7 days** between any two. A promise
pauses escalation until 2 days after the promised date; a broken promise resumes at
the tier it paused at, never back at polite. A dispute never enters the cadence at all.

## 7. Safety mechanisms

- **Reminder cap** — enforced by a database `CHECK`, not just code. The naive baseline
  in our own evaluation could not be recorded, because the schema refused a 4th reminder
- **Cooldown** — 7 days minimum, outranks the tier schedule
- **Banned language** — 30+ patterns, matched on normalized text so spacing,
  punctuation, and unicode lookalikes do not evade it. Runs on the model's output
- **Dispute routing** — straight to a human, never drafted
- **Append-only audit log** — a database trigger rejects `UPDATE`/`DELETE` for every
  role, including the owner
- **Auth** — every endpoint serving customer data or changing state requires a session
  from an active named account or a service admin key. Operators are independently
  revocable; auditors cannot mutate. Health checks are the only data-plane exception
- **Email outbox** — the send intent is committed before provider I/O, workers use
  expiring leases, and crashes are recovered. Delivery is at-least-once without
  provider support; the stable idempotency key upgrades providers that honor it
- **Prompt injection** — customer replies are wrapped and labelled as data, but the
  real defence is structural: the extraction function returns a value and has no
  access to money, mail, or invoice status

## 8. Failure handling

| Failure | What happens |
|---|---|
| Email bounces | Recorded as a failed *attempt*. Tier is **not** consumed. Retried with backoff (5m→2h, 5 attempts), then surfaced to an operator |
| Webhook processing fails | Stored with `status=failed`, retried with backoff (30s→15m, 6 attempts), visible in the exceptions queue, manually retryable |
| Duplicate webhook | Rejected by a unique index on the provider event id. Answered 200 so Razorpay stops |
| Out-of-order webhook | Amounts applied with `max()` — a stale event cannot walk a balance backwards |
| Payment Link cancellation fails | The payment stays recorded. Cancellation becomes a retryable task; it can never undo reconciliation |
| Gemini unavailable | Fails over to the fallback model, then to deterministic rules and templates |
| Razorpay rate limit | Recognised as transient (Razorpay labels 429 as a *bad request*) and retried with backoff |
| Two cycles at once | A Postgres advisory lock on a dedicated connection; a process/connection crash releases the session lock |

## 9. Evaluation

```bash
cd backend && uv run python -m eval.run_eval --compare-baselines
```

Runs the **real** policy engine, recovery cycle, and webhook handler against 150
held-out invoices over 45 simulated days. Razorpay and email are mocked at the
integration boundary; nothing else is. Ground-truth labels live only in the CSV and are
stripped at the ingestion boundary, so the classifier cannot see what it is scored
against.

Fixed seed → identical results. The behaviour model was fixed before any result was
looked at, and is stated in `eval/config.py`.

## 10. Setup

**Requires:** Python 3.13, `uv`, Node 20+, PostgreSQL 17.

```bash
cd backend
uv sync
cp .env.example .env          # fill in the values below
createdb vasooli
uv run alembic upgrade head
uv run python -m scripts.manage_operator create owner --display-name "Owner" --role admin
uv run python -m scripts.demo_reset
uv run uvicorn app.main:app --reload
```

```bash
cd frontend
npm install
cp .env.example .env.local    # NEXT_PUBLIC_API_URL and the shared backend SESSION_SECRET
npm run dev
```

**Environment variables**

| Variable | Purpose | Notes |
|---|---|---|
| `DATABASE_URL` | Postgres connection | |
| `RAZORPAY_KEY_ID` / `_SECRET` | Payment Links | **Test keys only** (`rzp_test_…`) |
| `RAZORPAY_WEBHOOK_SECRET` | Signature verification | From the dashboard webhook |
| `GOOGLE_API_KEY` | Gemini | Free tier is **20 requests/day per model** |
| `RESEND_API_KEY` | Email | |
| `EMAIL_DRY_RUN` | Record without sending | `true` unless demoing |
| `EMAIL_REDIRECT_TO` | Send everything here instead | **Required** to send live |
| `ADMIN_API_KEY` | Service credential | Never exposed to the browser |
| `RESEND_INBOUND_WEBHOOK_SECRET` | Native inbound signature | From Resend webhook settings |
| `EMAIL_REPLY_TO_DOMAIN` | Invoice reply routing | Verified receiving-enabled Resend domain |
| `SESSION_SECRET` | Signs backend-issued operator sessions | Same value in backend and frontend |

## 11. Demo instructions

See [`Docs/DEMO.md`](Docs/DEMO.md). The offline fallback at
[`Docs/assets/payment-webhook-fallback.gif`](Docs/assets/payment-webhook-fallback.gif)
combines real Razorpay Test Mode checkout captures with a clearly labelled local
signed-webhook replay; it does not claim a provider-originated webhook.

## 12. Testing

The suite is hermetic: `tests/conftest.py` pins every external integration to a
placeholder and asserts at session start that live email and live LLM calls are
impossible. Tests never touch the network, the dev database, or your inbox.

```bash
cd backend
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run alembic check             # no schema drift
uv run --with pip-audit pip-audit
```

```bash
cd frontend
npm run lint                     # eslint
npx tsc --noEmit                 # type check
npm test
npm run build
npm audit --audit-level=high
```


## 13. Deployment

Backend runs from `backend/Dockerfile` in Docker Compose on AWS EC2; Caddy terminates
TLS, and PostgreSQL runs on encrypted private Multi-AZ RDS so it is not in the EC2
failure domain. The frontend is deployed on Vercel. See `deploy/README.md` for the
exact migration order, health checks, mandatory off-host backups, restore drills, and
external dead-man alerts.

## 14. Known limitations

Stated plainly, because a smaller honest demo beats a fake impressive one.

- **Smart Collect is not used.** Razorpay does not offer it for this business type.
  Collection is via Payment Links
- **Payment Links are capped at ₹50,000** on this account, so the synthetic ledger uses
  smaller invoices than a real B2B book would
- **Inbound email is live.** `vasooli.space` is verified in Resend for both sending
  and receiving, and the Svix-signed webhook stores the full message, deduplicates it,
  correlates the sender to the invoice customer, and runs the same reply logic. The
  simulated-reply endpoint is disabled in production and returns 403
- **All outbound email is redirected** to a single inbox. The 60 synthetic customers
  have invented domains
- **Gemini free tier is 20 requests/day per model.** A full cycle over 8 invoices uses
  roughly 14. When exhausted, Vasooli falls back to deterministic templates — visibly
  labelled in the UI
- **Single merchant, named operators.** Humans now have independent DB credentials,
  admin/operator/auditor roles, account lockout, and revocable sessions. This is real
  per-user IAM for one merchant, not multi-tenant resource isolation or SSO/MFA
- **Email transport is at-least-once without provider cooperation.** The database
  outbox prevents lost intent and recovers expired worker leases. Exactly-once
  delivery still requires the provider to honor the stable idempotency key
- **The evaluation's customers are simulated**, driven by a stated behaviour model. It
  measures the policy, not real human behaviour

## 15. What production would require

Vasooli today is a **single-merchant system on test-mode payment keys**. Everything in
it is real — real Razorpay, real email in and out, real model calls, real reconciliation
— but it serves one merchant, and it has never moved a real customer's money.

This section exists so that gap is stated rather than left for a reader to discover.
The work below is deliberately *not* built: each item is a real engineering commitment,
and shipping a half-version of any of them would be worse than the honest absence.

| | What it means | Why it is not here |
|---|---|---|
| **Multi-tenancy** | Every query scoped to a merchant, enforced at the database, not in application code | Row-level isolation has to be right on day one. Retrofitting tenant scoping onto a single-tenant schema is how cross-tenant data leaks happen |
| **Live payment keys** | Real money, real settlement, real refunds | Requires completed KYC, a settlement account, and a reconciliation process that survives a disputed chargeback — none of which is demonstrable in a hackathon |
| **Merchant onboarding** | Sign-up, ledger import, domain verification for sending | Each merchant needs their own verified sending domain, or their reminders land in spam under someone else's reputation |
| **SSO / MFA** | Real identity, not a shared operator account | Per-user IAM with roles, lockout and revocable sessions already exists; federated identity does not |
| **Data retention and erasure** | A defined lifetime for customer messages, and a way to delete them on request | The audit log is append-only by database trigger — deliberately. Erasure and immutability have to be reconciled explicitly, not bolted on |
| **Horizontal scale** | More than one worker running cycles | The advisory lock already makes a second worker safe rather than harmful, so this is a capacity question, not a correctness one |

**What is production-shaped already**, and would carry over unchanged: the policy
engine, the AI/deterministic boundary and its architecture tests, idempotent webhook
handling with running-total semantics, the append-only audit trail, bounded retries
with backoff, the durable email outbox, and the deployment's migration-on-boot,
health-checked container setup.

The honest summary: the *recovery engine* is production-grade; the *product around it*
is not, and the list above is what standing that up actually costs.

