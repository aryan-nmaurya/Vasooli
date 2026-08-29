# Turning on real email, in and out

Everything below is configuration. The code is written and tested — the inbound
adapter verifies Svix signatures, correlates the sender against the invoice thread,
deduplicates on the provider event id, and stores the message. What it has never had
is a domain to receive at.

Run this at any point to see exactly where you are:

```bash
cd backend && uv run python -m scripts.preflight --host https://api.yourdomain.com
```

---

## The blocker: you need a domain

There is no way around this, and it is worth being blunt about it because it gates
more than email:

| Needs a domain | Why |
|---|---|
| Inbound customer replies | Email is delivered by MX record. You cannot receive mail at an IP address, and you cannot receive at `resend.dev` — that is Resend's domain, not yours. |
| Razorpay webhooks | Razorpay delivers over HTTPS to a valid certificate. A bare EC2 IP cannot have one. |
| Outbound that isn't spam-foldered | `onboarding@resend.dev` is a shared sender. Fine for a test to yourself; poor for anything you want to look real. |

Cost: roughly ₹700–1,000/year. Any registrar. Buy the cheapest `.xyz`, `.site` or
`.in` you can — nothing about this depends on the name.

**Until you own one, real inbound email is impossible and your Razorpay webhooks are
probably not being delivered either.** Both problems have the same £10 solution.

---

## 1. Point the domain at the box

```
api.yourdomain.com.    A    <your EC2 elastic IP>
```

Then follow `deploy/README.md` §3 — Caddy issues the certificate automatically once
that record resolves.

```bash
dig +short api.yourdomain.com     # must print your IP before you continue
```

---

## 2. Resend: verify the domain for sending

1. resend.com → **Domains** → Add Domain → `yourdomain.com`
2. Add the DKIM/SPF records it shows to your registrar's DNS
3. Wait for **Verified**

Then create a fresh API key (**your current one returns 401**) at resend.com/api-keys
and set it:

```
RESEND_API_KEY=re_...
EMAIL_FROM=Vasooli <billing@yourdomain.com>
```

Check it:

```bash
uv run python -m scripts.verify_live_integrations --resend --send-test-email
```

That sends exactly one mail, to `EMAIL_REDIRECT_TO`, and nowhere else.

---

## 3. Resend: turn on receiving

1. Domains → `yourdomain.com` → **Receiving** → add the **MX record** it gives you
2. Webhooks → Add Endpoint → `https://api.yourdomain.com/api/webhooks/resend/inbound`
3. Subscribe to **`email.received`**
4. Copy the **Signing Secret** (`whsec_...`)

```
EMAIL_REPLY_TO_DOMAIN=yourdomain.com
RESEND_INBOUND_WEBHOOK_SECRET=whsec_...
```

`EMAIL_REPLY_TO_DOMAIN` is load-bearing: reminders set `Reply-To:
invoice-<number>@<that domain>`, and that address is how an inbound message is matched
back to an invoice. Point it at a domain whose MX is not Resend's and replies vanish
silently.

---

## 4. Open the gate

```
EMAIL_DRY_RUN=false
EMAIL_REDIRECT_TO=you@gmail.com     # keep this until you have watched it work
ALLOW_SIMULATED_REPLIES=false
```

`assert_safe_to_send()` refuses to send live unless the inbound secret and a real
reply domain are both set. That is deliberate: a system that emails customers but
cannot receive their answers is worse than one that does neither.

---

## 5. Prove it end to end

```bash
uv run python -m scripts.preflight --host https://api.yourdomain.com
```

Everything green, then:

1. Dashboard → an overdue invoice → **Run recovery cycle**
2. The reminder arrives in your inbox. Check `Reply-To:` reads
   `invoice-INV-3003@yourdomain.com`
3. **Reply from your mail client**, as a customer would: *"We were billed for 12 units
   but only received 9. Please check before we pay."*

   This works because `EMAIL_REDIRECT_TO` is set. Vasooli rerouted the reminder to
   your inbox, so it accepts a reply *from* that inbox as the intended round-trip —
   the message still has to carry a valid signature and be addressed to that
   invoice's unique alias, and the audit trail records your address as the sender
   rather than pretending the customer wrote it. Once you drop the redirect and mail
   real customers, only their own addresses correlate.
4. Within seconds the invoice page shows: the reply in the conversation, the AI's
   reading of it, `RECOVERY PAUSED` attributed to the policy engine, and a dispute
   case awaiting your decision

Step 3 is the whole demo. Nothing is injected; the message travels Gmail → MX →
Resend → signed webhook → Vasooli.

---

## What breaks, and how you will know

| Symptom | Cause |
|---|---|
| Reminder never arrives | `RESEND_API_KEY` invalid, or domain not verified. `preflight` catches both. |
| Reply arrives in your inbox but Vasooli never reacts | MX not pointed at Resend, or the webhook endpoint is not registered. `preflight` checks the MX. |
| Webhook returns 400 | `RESEND_INBOUND_WEBHOOK_SECRET` does not match the endpoint's signing secret. |
| Webhook returns 403 | Correct signature, but the sender is neither that invoice's customer nor `EMAIL_REDIRECT_TO`. Reply from one of those two. |
| Reminder sends, reply ignored, no errors | `EMAIL_REPLY_TO_DOMAIN` still `example.com`. The reply went to a domain you do not own. |

A 403 here is the system working. A valid provider signature proves the message was
delivered; it does not prove the `From:` address owns the invoice. Those are separate
checks on purpose.
