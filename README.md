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
| AI cannot invent a figure | Required figures must be present *and* nothing else money-shaped may be — every amount, link, and reference in a draft is matched against an allowlist, and a draft with an extra one is discarded for the template |
| Authentication | Named DB accounts, roles, lockout/revocation, every endpoint verified unauthenticated |
| Outbound email | Durable leased outbox; the Resend key authenticates and `vasooli.space` is verified for sending. Mail is redirected to an operator inbox and cannot be pointed at a customer |
| Delivery and bounce outcomes | `sent` means the provider accepted the message and is named that way throughout. Delivery, bounce, deferral, and spam complaints arrive on a signed webhook and are applied separately; a hard bounce ends the cadence for that invoice rather than advancing it |
| Payments outside a Vasooli link | Bank transfer, UPI, cheque, and agreed adjustments are recorded against their own invoice column, never mixed with the running total Razorpay reports. Recorded under a named operator, reversible, and marked `operator_asserted` in the trail |
| Manual matching of an unmatched payment | An operator picks the invoice; the amount is read from the stored webhook payload, not from the request |
| Inbound reprocessing | A reply that failed to parse is retried with bounded backoff and can be reprocessed by hand after the attempts run out |
| Scheduler evidence | Every job run is recorded before and after, so the dashboard reports the last successful cycle rather than the fact that the scheduler is configured |
| Inbound email | `vasooli.space` is receiving-enabled with MX records in place, and the Svix-signed webhook stores, deduplicates and correlates a reply before acting on it |
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
| Live Razorpay webhook end-to-end | The handler is tested against signed payloads, but Razorpay can only deliver to a host presenting a valid certificate. Until the deployment's TLS is trusted, payment confirmation in the live environment comes from the authenticated Razorpay sync rather than a pushed webhook |

**`scripts/preflight` is the authority, not this file.** Prose goes stale between a
change and the next edit; the preflight does not. Run it immediately before recording
or demonstrating anything, and believe it over any sentence written here:

```bash
cd backend && uv run python -m scripts.preflight --host https://<your-host>
```

It checks configuration, DNS, Razorpay, Resend and Gemini, and reports what is
missing and the remedy for each — in the order they break. At the last run: **13
passed, 0 failed**, with the public-host check skipped because no `--host` was given.

**Nothing above is presented as real anywhere else in this repository.** If you find a
contrary claim, it is a bug.

## For reviewers

**Live:** [vasooli.space/guide](https://vasooli.space/guide) — a public page, no login
required. It states what is real, what is test-mode, and where to click first.

**Credentials** are issued per reviewer on request, as an `operator` account: a
reviewer should be able to run a cycle, redirect mail to their own inbox, reply to it,
and watch the dispute open — evaluating a recovery system read-only means taking every
interesting claim on trust. An `auditor` role also exists and is enforced in
`app/api/deps.py`, which refuses every non-GET request; it is the right choice for
someone who should see the evidence without being able to send mail.

If you have ten minutes and want to check the central claim rather than the interface:

| Read | What it proves |
|---|---|
| `backend/tests/architecture/test_layering.py` | The AI layer cannot import a mailer, a DB session, or the Razorpay client. Parsed from the AST, so the build fails if it is ever crossed |
| `backend/app/policy/disputes.py` | The decision to pause recovery is a pure function. No model, no clock, no database |
| `backend/tests/integration/test_disputes.py` | When recovery pauses, the audit trail attributes the *reading* to the AI and the *decision* to the policy engine — asserted, not described |
| `backend/eval/` + `backend/eval/out/results.csv` | The three-arm evaluation, including the arm where a naive chaser beats us |

```bash
cd backend && uv run pytest -q          # 836 tests
uv run python -m scripts.preflight      # every integration, live
```


## Reviewer settings (demo controls)

The cadence fires at 3, 10 and 21 days overdue, and reminder mail is redirected away
from customers. Both are correct for a real merchant and make the product impossible
to evaluate in a sitting, so a signed-in operator gets a **Settings** panel — bottom
left of the dashboard — with two controls.

Gated behind `DEMO_CONTROLS_ENABLED`, which defaults to **false**. A real
multi-merchant deployment leaves it off and neither control exists.

### Time machine

Moves a simulated clock forward, then runs the ordinary recovery cycle against the
later date. It fabricates nothing: `run_recovery_cycle` is the same function the
scheduler calls, and the policy engine still decides what is due under the same rules.
Only the date the system believes it is has moved — and the panel shows the simulated
and real dates side by side rather than hiding the difference.

Every move writes an audit row (`demo_clock_advanced`, `demo_clock_reset`) naming who
moved it and by how much, because the clock changes what "now" means for every
decision underneath it.

This is deliberately **not** `DEMO_TIME_OFFSET_DAYS`. That one is a static boot-time
shift, and `assert_production_safe` refuses to start with it set — a forgotten offset
in a real deployment corrupts overdue maths silently. The runtime clock starts at
zero, moves only through an audited endpoint, is visible in the UI whenever it is not
zero, and winds back without a redeploy.

### Send reminders to

Points reminder mail at the reviewer's own inbox. This is the only way to exercise the
inbound path without access to the deployment's environment: receive a real reminder,
reply to it, and watch the reply come back through the signed Resend webhook and open
a dispute.

Two properties make it safe to expose:

- **It can only move the redirect, never remove it.** Clearing falls back to
  `EMAIL_REDIRECT_TO`, so no sequence of calls here results in mail reaching the
  invented customer addresses in the seeded ledger.
- **The send path and the inbound path read the same value.** Both go through
  `app.core.runtime.effective_email_redirect`, so a reviewer who redirects mail to
  their own inbox can also reply from it — reading the raw setting in one place and
  the override in the other would accept the reminder and then refuse the answer.

Changes are audited as `demo_email_redirected`, recording the old and new address.

**Known constraint:** these overrides live in module state, mirrored to the
`demo_settings` row and rehydrated at startup. That makes them per-process. The
deployment runs a single uvicorn process with the scheduler inside it, so the endpoint
that sets an override, the cycle that sends mail and the webhook that accepts the
reply all share one copy. Running multiple workers would break that and the reads
would need to move to the database.


## Getting the ledger in and out

A recovery system that can only be loaded by a seed script is a demo. **Import** and
**Export** sit next to each other in the dashboard header because they are the same
job in two directions.

### Export

`GET /api/export/{recovered|overview|invoices}?format={csv|xlsx|pdf}`

| Dataset | Contents |
|---|---|
| `invoices` | The recovery queue, honouring the `status` and `reason` filters currently on screen — so a download and the page can never disagree about which rows are in scope |
| `recovered` | Settled invoices with what was paid and when |
| `overview` | Recovery rate, totals, and the counts by status and reason |

The three renderers share one `Sheet` structure, so a column added once appears in all
three formats. Money is stored in paise and converted to rupees at exactly one place
(`_paise_to_rupees`); the xlsx writes real numeric cells with an Indian-grouped format
rather than pre-formatted strings, so the numbers stay summable in Excel.

Downloads pass through `frontend/app/api/download/[...path]`, a separate proxy from
the JSON one — that one reads the body as text and stamps every response
`application/json`, which is right for the dashboard and silently corrupts an `.xlsx`.
Paths are allowlisted and only `format`, `status` and `reason` are forwarded: an
export route that accepts an arbitrary path is a data-exfiltration route with a
friendly name.

### Import

`POST /api/invoices/import` (multipart, `dry_run` defaults to **true**)

Two steps, because importing four hundred rows blind and discovering afterwards that
row 47 was malformed is the version that wastes an afternoon:

1. **Preview.** The file is parsed and nothing is written. The response reports how
   many rows would import, which invoice numbers are already in the ledger, which
   columns were ignored, and — for anything unparseable — the **spreadsheet line
   number**, so the fix is a jump rather than a bisect.
2. **Commit.** Only an explicit `dry_run=false` writes. The write itself is
   `ingest_batch`, the same path the seed script uses, so imported invoices are
   ordinary invoices: idempotent on invoice number, customers created as needed.

One malformed row does not cost the merchant the other 399 — bad rows are skipped and
reported, good rows import.

A template is available at `GET /api/invoices/import/template`, generated from
`InvoiceIngestRow.model_fields` so it cannot drift from what the parser accepts. The
importer also understands the human-readable headings the exports emit ("Invoice",
"Amount (₹)", "Issued"), so an exported ledger imports straight back in; an export
that cannot be re-imported is a one-way door.

Limits: 5 MB, 5,000 rows, UTF-8. Both require a signed-in account or the service key.
Exports are open to auditors as well — producing evidence is the auditor's job — while
the import commit is a write and is refused for that role.


## 3. Architecture

```
OVERDUE INVOICE
      ↓
  DIAGNOSIS ................ AI explains, deterministic rules decide
      ↓
  POLICY ENGINE ............ pure Python, 10 checks, no model involved
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
| Whether money arrived | **Deterministic** | Razorpay only — a signature-verified webhook, or an authenticated response to a call *we* made to Razorpay (the hourly sync). Both are recorded with their provenance; neither is a model's output |
| How much was paid | **Deterministic** | Integer paise, running total, `max()` |
| Whether to send a reminder | **Deterministic** | `app/policy` — 10 checks, pure functions |
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
  role. A table owner can still TRUNCATE it (a row trigger does not fire on
  TRUNCATE) or drop the trigger — so this is tamper-evidence against the
  application and ordinary DML, not against a determined DBA
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
  roughly 14 — and Dry run costs the same, since it diagnoses and drafts through the
  same AI path and only skips the send. When exhausted, Vasooli falls back to
  deterministic templates — visibly labelled in the UI, never a silent failure
- **Single merchant, named operators.** Humans now have independent DB credentials,
  admin/operator/auditor roles, account lockout, and revocable sessions. This is real
  per-user IAM for one merchant, not multi-tenant resource isolation or SSO/MFA
- **Email transport is at-least-once without provider cooperation.** The database
  outbox prevents lost intent and recovers expired worker leases. Exactly-once
  delivery still requires the provider to honor the stable idempotency key
- **The evaluation's customers are simulated**, driven by a stated behaviour model. It
  measures the policy, not real human behaviour
- **Invoices are imported, not discovered.** There is no connector to an accounting
  system, ERP, or Razorpay's own invoice objects. A CSV or an API call is how the
  ledger gets in, and re-importing an existing invoice number is skipped rather than
  synchronised — so a change in the merchant's books does not reach Vasooli
- **Payments made outside a Vasooli payment link are recorded by a person, not
  detected.** There is no bank feed. The record-a-payment flow is what stops a customer
  who paid by NEFT from being chased, and it depends on somebody entering it
- **Refunds, chargebacks, and credit notes are not ingested.** A recorded payment can be
  reversed by hand, which is enough to put an invoice back into the queue, but Razorpay
  refund and dispute events are not consumed and there is no chargeback lifecycle
- **A reversed payment does not reprovision a payment link.** Razorpay links cannot be
  un-cancelled, so an invoice that becomes owed again needs a new link created by hand.
  The reversal says so rather than leaving the customer with no way to pay

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

