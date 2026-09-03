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

> **Check the Gemini quota the day before, not on the day.** The free tier allows 20
> requests per day *per model*, and one recovery cycle over the eight seeded invoices
> needs a diagnosis and a draft for each — so a single rehearsal can exhaust the day's
> allowance and leave the real demo running on deterministic fallbacks with the runtime
> banner reading `ai: degraded`. A partly-spent quota fails intermittently, which reads
> as flaky models rather than as a limit. Enable billing on the Google Cloud project
> behind the key, or rehearse with `Dry run` (step 6), which still calls the models —
> `use_llm` is independent of `dry_run`, so a dry run costs quota exactly like a real
> one. Rehearsing on a different day from the demo is the cheap version of this fix.

### 3b. Where to run the demo

**Run the guided demo from `localhost:3000`, not from the deployed site.**

The Time Machine — the control that compresses the 3/10/21-day cadence into two
minutes — needs `DEMO_CONTROLS_ENABLED`, and production refuses to boot with it on
unless `ALLOW_DEMO_CONTROLS_IN_PRODUCTION` is also set. It is deliberately off there,
because the demo clock is process-global: advancing it shifts `utcnow()` for every
tenant, including overdue counts, trial end dates and session expiry for real
merchants. Both flags must always be changed together — setting the override to false
while the feature stays on is the exact combination that crash-loops the API.

A second reason: `mentor` and `reviewer` are `auditor` accounts, and moving the clock
is a write, so `POST /api/demo/advance` returns 403 for them. Even with the controls
on, a reviewer could not drive it themselves.

Use the deployed site as proof the system is really running — health, live Razorpay
plans, row-level security under a restricted role — and drive the guided story
locally, where the clock is safe and you hold an admin account.

### 4. Deployed backend

Use the deployed TLS endpoint for the production demo:

```bash
curl https://api.vasooli.space/health
```

The bare Elastic IP and its `sslip.io` alias no longer answer HTTPS at all — Caddy
holds a certificate only for `api.vasooli.space` now (see `deploy/Caddyfile`), and a
request presenting any other hostname fails the TLS handshake before it ever reaches
the app. If a check against the old address ever gets pasted into a review again,
that failure is the certificate, not the server — verify against the domain above
before concluding anything is down.

For a fully local rehearsal only, a public tunnel is still required. Never leave a
temporary tunnel URL configured in Razorpay after the rehearsal.

> **Test the venue network before you present.** Some networks make a perfectly healthy
> deployment look dead, and each failure mode looks like an outage:
>
> - **DNS sinkholing.** On the college wifi, `vasooli.space` resolves to
>   `sinkhole.paloaltonetworks.com` — including through `1.1.1.1` and `8.8.8.8`, because
>   the interception is transparent. Every request then fails with `Connection reset by
>   peer`. Confirm from a second path before concluding anything:
>   `curl -s --resolve api.vasooli.space:443:13.204.55.131 https://api.vasooli.space/health`
> - **IPv6-only carriers.** One hotspot ran IPv6-only with NAT64, so the bare IPv4
>   address had no route at all while hostnames worked fine (DNS64 synthesises the AAAA
>   record). `curl` succeeded and `ssh 13.204.55.131` timed out, which reads as a
>   firewall but is not one. The giveaway is a `64:ff9b::` address in `curl -v` output.
> - **Outbound SSH.** Blocked on the college wifi, and Cloudflare WARP does not carry
>   port 22 either — so with WARP on the site loads but deploys fail, and with it off
>   deploys work but the site does not load. A phone hotspot is the reliable path.
>
> This has produced five separate false "the server is down" conclusions. It is always
> worth thirty seconds with `--resolve` before believing one.

### 5. Razorpay webhook

Dashboard → **Test Mode** → Account & Settings → Webhooks → **Add New Webhook**

| Field | Value |
|---|---|
| Webhook URL | `https://api.vasooli.space/api/webhooks/razorpay` |
| Secret | the same value as `RAZORPAY_WEBHOOK_SECRET` |
| Active Events | search `payment_link` → tick **`payment_link.paid`** and **`payment_link.partially_paid`** only |

Do not subscribe to `payment.captured` or `payment.authorized` — Vasooli ignores them,
and each one is noise arriving at your endpoint.

**Restart the backend** after changing `RAZORPAY_WEBHOOK_SECRET`, or every real
webhook is rejected as a bad signature.

### 6. Rehearse with Dry run, not the real thing

Gemini's free tier is 20 requests/day and one full cycle over 8 invoices uses roughly
14. **Dry run sends no email, but it still diagnoses and drafts through the same AI
path**, so it spends the same calls as a real cycle — use it to rehearse the flow,
not to save quota. If the free tier runs out mid-demo, Vasooli falls back to
deterministic templates automatically, visibly labelled in the UI; it degrades, it
does not block.

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

**This is a real email round-trip.** `vasooli.space` is verified in Resend for both
sending and receiving, so do not reach for the simulation control — it ships disabled
(`ALLOW_SIMULATED_REPLIES=false`) and returns 403.

1. Open **Settings** (bottom left) → **Send reminders to** → your own inbox. Save.
2. Press **Run recovery cycle**. A reminder arrives in that inbox, from this domain.
3. **Reply to it** in your mail client, saying you will pay on Friday.

The reply returns through `POST /api/webhooks/resend/inbound`, which verifies the Svix
signature, deduplicates on event id, correlates the sender against the invoice thread,
and only then hands the text to the extractor.

Press **Dry run**: that invoice is now *held*.

> The redirect can move between inboxes but cannot be cleared to reach a customer —
> both the send path and the inbound authorization read the same value, so an address
> you can receive at is an address you can reply from.

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
