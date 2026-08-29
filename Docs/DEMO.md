# Demo procedure

About 4 minutes. Everything below is **real** unless marked **DEMO SIMULATION**.

---

## Part 1 — Setup (do this the day before, not on the day)

### 1. Postgres

```bash
brew services start postgresql@17
pg_isready -h localhost -p 5432
```

A dead database looks exactly like a broken app. Check this first, every time.

### 2. Seed the demo ledger

```bash
cd backend && uv run python -m scripts.demo_reset
```

Eight invoices, each with a real Razorpay Payment Link. Takes ~90 seconds — Razorpay
test mode allows about six link creations per minute.

### 3. Environment

`backend/.env` must have:

| Variable | Value | Why |
|---|---|---|
| `RAZORPAY_KEY_ID` | `rzp_test_…` | **Test keys only.** A live key takes real money |
| `RAZORPAY_KEY_SECRET` | your test secret | |
| `RAZORPAY_WEBHOOK_SECRET` | must match the dashboard (step 5) | Signature verification fails otherwise |
| `EMAIL_DRY_RUN` | `false` to send real mail | |
| `EMAIL_REDIRECT_TO` | your inbox | Required before live sending |
| operator account | create with `scripts.manage_operator` | Your individual login |
| `RESEND_INBOUND_WEBHOOK_SECRET` | `whsec_…` | Native inbound verification |
| `GOOGLE_API_KEY` | your key | **20 requests/day on the free tier** |

**Never commit these.** `.env` is gitignored; verify with `git check-ignore backend/.env`.

### 4. Deployed backend

Use the deployed TLS endpoint for the production demo:

```bash
curl https://api-13-204-55-131.sslip.io/health
```

For a fully local rehearsal only, a public tunnel is still required. Never leave a
temporary tunnel URL configured in Razorpay after the rehearsal.

### 5. Razorpay webhook

Dashboard → **Test Mode** → Account & Settings → Webhooks → **Add New Webhook**

| Field | Value |
|---|---|
| Webhook URL | `https://api-13-204-55-131.sslip.io/api/webhooks/razorpay` |
| Secret | the same value as `RAZORPAY_WEBHOOK_SECRET` |
| Active Events | search `payment_link` → tick **`payment_link.paid`** and **`payment_link.partially_paid`** only |

Do not subscribe to `payment.captured` or `payment.authorized` — Vasooli ignores them,
and each one is noise arriving at your endpoint.

**Restart the backend** after changing `RAZORPAY_WEBHOOK_SECRET`, or every real
webhook is rejected as a bad signature.

### 6. Rehearse with Dry run, not the real thing

Gemini's free tier is 20 requests/day and one full cycle over 8 invoices uses roughly
14. Use the **Dry run** button for rehearsals — it evaluates everything and sends
nothing.

---

## Part 2 — The demo

### Offline payment fallback

Keep [`assets/payment-webhook-fallback.gif`](assets/payment-webhook-fallback.gif)
open in a separate tab before presenting. If Razorpay, mobile internet, or the webhook
route fails on stage, play this pre-rendered walkthrough and state that it is the
offline fallback. Its first two frames are captured from a real ₹1 Razorpay Test Mode
checkout using synthetic public test data. The webhook/reconciliation frames are a
deterministic rendering of the locally signed replay path—not footage of a provider-
originated webhook—and are labelled that way in the artifact.
After this working tree is committed and deployed, the public copy will be available
at `https://vasooli-phi.vercel.app/demo/payment-webhook-fallback.gif` without signing
in. Until then that URL may still show the previous artifact.

Regenerate it after changing payment copy or state names:

```bash
cd backend
uv run --with pillow python ../scripts/generate_payment_webhook_fallback.py
cd ..
```

### 1 — The queue (20s)

Sign in at `https://vasooli-phi.vercel.app`. Eight overdue invoices, largest outstanding first.

Point at **Total overdue**, **Recovery rate** (by value, not invoice count), and the
**Why** column — every row explains itself.

### 2 — Why is Vasooli doing this? (45s)

Open **INV-3003 (ABC Traders, ₹34,000, 10 days overdue)**.

- **Why card** — one sentence: what is happening and what happens next
- **Diagnosis**, badged `AI`
- **Timeline** — every step badged `SYSTEM` / `AI` / `POLICY` / `RAZORPAY`
- **The policy card** — nine checks, each ✓ or ✗

> "The model wrote the words. This decided whether they could be sent."

### 3 — The system refusing to act (30s)

Open **INV-3005 (Kiran & Co)** — disputed. Zero reminders, status *human review*. It
never entered the cadence.

### 4 — A promise pausing everything (30s)

**DEMO SIMULATION.** Under **Demo Controls**, press **Promise to pay** → **Send reply**.

This step deliberately uses the labelled simulation control. A native, Svix-verified
Resend Receiving endpoint is implemented, but it requires an enabled provider webhook
and verified receiving domain; the runtime banner reports the actual mode. Both
paths use the same extraction, validation, and promise handling.

Press **Dry run**: that invoice is now *held*.

### 5 — The real payment (60s) ← the technical centre

Open **INV-3006 (Meridian Packaging, ₹22,000)**. Copy its Payment Link into a new tab.

Pay with a Razorpay **test card**: `4111 1111 1111 1111`, any future expiry, any CVV.

Within about three seconds, with no refresh:

```
Razorpay payment
   → webhook received
   → signature verified          (HMAC-SHA256 over the raw body)
   → event persisted             (unique provider event id)
   → idempotency check
   → payment reconciled          (integer paise, running total)
   → invoice recovered
   → Payment Link cancelled      ← recovery stops at the payment route too
   → audit log updated
   → Recovered tile flashes green
```

**Verify it on the backend:**

```bash
psql -d vasooli -tAc "
SELECT provider_event_id, event_type, status, match_strategy
FROM reconciliation_events ORDER BY received_at DESC LIMIT 1;"

psql -d vasooli -tAc "
SELECT invoice_number, status, amount_paid_paise FROM invoices
WHERE invoice_number='INV-3006';"
```

Expect `status=processed`, `match_strategy=payment_link_id`, invoice `recovered`.

### 6 — Replay the same webhook (30s)

```bash
cd backend && uv run python -m scripts.replay_webhook --invoice INV-3006 --times 5
```

**DEMO SIMULATION** — the payload is generated and signed locally. It proves *our*
handling is idempotent; it does not prove what Razorpay sends. (Step 5 proved that.)

Five deliveries, one counted. The other four: `duplicate_ignored`.

> "Razorpay delivers at-least-once. The dedup key is a unique index in Postgres — it
> survives a restart and is shared across workers, which an in-memory set is not."

### 7 — Failure and recovery (60s) ← the reliability centre

A payment arrives that Vasooli cannot match, because the mapping for its link is
missing:

```bash
uv run python -m scripts.demo_failure --invoice INV-3006
```

Show **Operational exceptions**: the event, the error, the attempt count, a Retry
button.

> "The webhook was answered 200, so Razorpay has stopped redelivering. That makes the
> failure ours to fix — so it is stored, retried with backoff, and put in front of a
> human."

Repair the mapping, then press **Retry** in the dashboard:

```bash
uv run python -m scripts.demo_failure --repair
```

```
FAILED → EXCEPTION QUEUE → REPAIR → RETRY → RECOVERED
```

**Optional — the case retrying cannot fix:**

```bash
uv run python -m scripts.demo_failure --kind unmatched
```

A payment matching nothing at all. Vasooli marks it terminal rather than retrying
forever, because a human has to work out what it was for.

---

## The webhook that never arrives

Worth knowing, because it happened during development: a real ₹9,500 payment was made
while no reachable webhook was running. Razorpay held the money; Vasooli showed the invoice
unpaid; no retry on Razorpay's side would ever have fixed it.

An hourly job asks Razorpay directly whether anything has been paid that Vasooli does
not know about, and reconciles it through the same path a webhook takes.

```bash
uv run python -c "
from sqlmodel import Session; from app.core.db import engine
from app.services.sync import sync_payment_links
print(sync_payment_links(Session(engine)))"
```

---

## If something goes wrong

| Symptom | Fix |
|---|---|
| Dashboard won't load | `pg_isready` — Postgres is probably down |
| Login fails | Backend restarted after the password changed? Only the backend knows it |
| Payment doesn't appear | Tunnel running? Webhook URL current? Run the sync above |
| Emails say `template_fallback` | Gemini's 20/day quota is spent. Say so — it's the failover working |
| Cycle says "already running" | A lock from a killed run; it clears itself in 10 minutes |
| Payment link 404s | Re-run `demo_reset` |

## If asked "is any of this fake?"

- **Real:** Payment Links, the payment, the webhook, signature verification,
  reconciliation, link cancellation, the direct sync
- **Real:** emails, sent through Resend, redirected to one inbox
- **Simulated:** customer replies are injected, not received — same code path, no
  inbound mail
- **Simulated:** the local webhook replay in step 6
- **Simulated:** the evaluation's customers, driven by a stated behaviour model
