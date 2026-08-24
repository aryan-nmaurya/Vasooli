> # ⚠️ ARCHIVED — PRE-BUILD PLANNING DOCUMENT
>
> **This describes the architecture as PLANNED, not as BUILT.** It is kept for the
> record of how the project was designed, and should not be read as a description of
> the current system.
>
> **What changed:** this document specifies Razorpay **Smart Collect / Virtual
> Accounts** — a dedicated bank account per invoice. Razorpay confirmed Smart Collect
> is **not available for this merchant's business type**, so collection was built on
> **Razorpay Payment Links** instead. Every reference below to virtual accounts,
> `virtual_account.credited`, or per-invoice bank details describes a design that was
> never implemented.
>
> **The canonical description of what actually exists is [`../../README.md`](../../README.md).**
>
> ---

# Vasooli

**AI-Powered B2B Receivables Recovery Agent**

**Track:** AI Revenue Recovery — Track 03
**Direction:** B2B receivables chaser, with a promise-to-pay tracker and reconciliation loop

**Core promise:** Chase. Track. Reconcile. Recover.

---

## One-line description

Vasooli is an AI agent that chases overdue B2B invoices on a bounded, compliant escalation schedule, tracks and enforces the promises customers make to pay, and automatically reconciles real incoming payments the moment they land — closing the loop from "money is overdue" to "money is recovered" without manual spreadsheet work.

---

## 1. The Problem

For a B2B merchant, revenue rarely disappears because a customer refuses to pay. It disappears because of process gaps around collection:

- **Follow-up is inconsistent.** Someone has to remember to chase every overdue invoice, at the right time, in the right tone. Too soft and it never gets paid; too aggressive and it damages a business relationship — or crosses into non-compliant debt-collection conduct.
- **Promises aren't tracked.** A customer says "I'll clear this by Friday." Nobody logs it. Friday passes, and the invoice quietly re-enters a generic reminder queue instead of an accountable one.
- **Reconciliation is manual.** When payment finally arrives — often a partial amount, from an unexpected account, via NEFT/RTGS with no clean reference — someone has to manually match it against the right invoice before the chase can even stop.

None of these are dramatic failures. They're small, boring leaks. But they compound into real, measurable overdue balances sitting uncollected at any given SMB — exactly the "revenue slipping away" that Track 03 asks you to close.

---

## 2. The Core Idea

Vasooli runs as a bounded, self-stopping recovery loop — not a one-shot script, and not an unbounded nag bot.

```
Overdue Invoice
      │
      ▼
Provision Virtual Account (Razorpay Smart Collect)
      │
      ▼
Diagnose likely reason invoice is at risk
(oversight / cash-constrained / dispute-likely / unresponsive)
      │
      ▼
Choose escalation tone (polite → firm → final)
      │
      ▼
Policy / Compliance Check  ← cadence caps, banned language, cooldowns
      │
      ▼
Send Reminder
      │
      ▼
   Customer replies?
      │
      ├── Promise to pay detected ──► Pause escalation, track promise
      │         │
      │         ├── Promise kept ──► Wait for payment
      │         └── Promise broken ──► Resume escalation, log broken promise
      │
      ▼
Razorpay Virtual Account receives payment
      │
      ▼
virtual_account.credited webhook (real, from Razorpay)
      │
      ▼
Auto-reconcile → invoice marked paid → drops out of queue
      │
      ▼
₹ Recovered
```

The distinction that matters: Vasooli doesn't just flag overdue invoices and stop. It acts on a policy, tracks what customers commit to, and verifies recovery against a real payment event — not an assumption.

---

## 3. What Vasooli Actually Does

### Stage 1 — Ingest & Provision

Vasooli ingests a batch of overdue invoices (synthetic data standing in for a merchant's receivables ledger: customer, amount, due date, terms, contact channel). For each invoice, it provisions a **real Razorpay Smart Collect virtual account** via the API, with `amount_expected` set to the invoice total. Every invoice gets one dedicated, trackable way to pay — no manual reference-matching required later.

### Stage 2 — Diagnose

Before choosing how to chase an invoice, Vasooli infers why it's likely at risk, using the signals actually available:

- Customer's historical payment reliability (on-time rate, past broken promises)
- Invoice size relative to the customer's typical order size
- Days past due
- Whether a previous promise on this invoice was already broken

**🔒 Locked schema — do not change once implementation starts.** Every downstream component (policy engine, dashboard, evaluation harness) is built against these exact values. If a change turns out to be necessary, update it here first, then propagate to every component below in the same sitting — never let one component drift from this definition.

**The four reason categories (exact, final):**

| Category | Definition — applies when |
|---|---|
| **Oversight** | Customer has a clean payment history (no prior late payments on record) and this is their first time overdue |
| **Cash-constrained** | Customer has paid late before, but has always eventually paid in full |
| **Dispute-likely** | Customer's reply contains a complaint, or the invoice already has a prior dispute note on file |
| **Unresponsive** | No reply received after the Tier 2 reminder was sent |

Output: exactly one of these four categories per invoice, plus a short plain-language explanation. This isn't a black-box score — it directly drives what happens next. **Dispute-likely always routes straight to human review, never through the automated cadence** — an automated nudge is the wrong tool for a disputed invoice.

### Stage 3 — Escalate (bounded, not infinite)

A fixed, compliant three-tier cadence, capped in every dimension. **Timings are exact day-counts, not ranges:**

| Tier | Timing (exact) | Tone | Content |
|---|---|---|---|
| 1 | **Day 3** overdue | Polite | Friendly reminder, invoice details, virtual account payment reference |
| 2 | **Day 10** overdue | Firm | Restates amount and due date, re-sends payment reference, asks for confirmation or a pay-by date |
| 3 | **Day 21** overdue | Final | Final notice tone, **automatically flags for human review** — Vasooli does not escalate beyond this on its own |

These three numbers — 3, 10, 21 — are the single most-referenced constants in the codebase (policy engine cadence checks, dashboard tier labels, evaluation harness ground-truth windows, seed data for demo invoices). Define them once as named constants at the start of the build and import them everywhere; never hardcode 3/10/21 a second time anywhere else in the code.

Hard rules enforced by policy, not by the language model:

- Maximum of 3 automated reminders before mandatory human handoff — never fully autonomous indefinitely
- Minimum cooldown between reminders (no same-week repeated contact)
- No threatening or legal-action language, ever — enforced as a rules layer independent of what the LLM drafts
- "Dispute-likely" cases skip the automated cadence entirely and go straight to human review

### Stage 4 — Track Promises

When a customer's reply implies a commitment ("I'll clear this by Friday," "paying next week"), Vasooli extracts the implied date and amount, **pauses escalation** until that date plus a short buffer, and logs the promise. If the promise is kept (payment arrives), the case resolves normally. If it's broken, escalation resumes automatically at the tone level it left off — not reset to polite — and the broken promise is logged and visible in the invoice's history. Repeated broken promises feed back into the diagnosis for future reminders.

### Stage 5 — Reconcile

The moment Razorpay confirms money has landed against a virtual account — even a partial or delayed transfer — it fires a real `virtual_account.credited` webhook. Vasooli matches this deterministically against the invoice via the virtual account's linked `customer_id`, marks it paid, and removes it from the active queue. This step is intentionally **not** LLM-driven — money-matching is deterministic, auditable logic, not a place for a language model to guess.

### Stage 6 — Audit Everything

Every action Vasooli takes — reminder sent, tone chosen and why, promise logged, promise broken, human escalation triggered, payment reconciled — is written to an append-only audit log, visible per invoice.

---

## 4. Razorpay Integration (Real, Not Mocked)

This is the part of the build that has to hold up under a technical panel's questions, so it's built on Razorpay's actual Smart Collect / Virtual Accounts primitive rather than a generic "mark as paid" button.

**Razorpay → Vasooli**

- `POST /v1/virtual_accounts` — create one virtual account per invoice, with `amount_expected` and `customer_id` set
- Webhook subscription to `virtual_account.credited` (and `virtual_account.created` for provisioning confirmation)
- In test mode, incoming payments can be simulated from the Razorpay dashboard to fire these webhooks on demand — no real money involved, but a genuinely real event path

**Vasooli → Razorpay**

- `GET /v1/virtual_accounts/{id}` — fetch current status/amount paid for reconciliation checks and dashboard refresh
- `POST /v1/virtual_accounts/{id}/close` — close the virtual account once an invoice is fully recovered or written off

**Why this matters for the pitch:** this is a real, production Razorpay primitive (used for exactly this purpose — large-payment collection via bank transfer) that most student teams won't reach for, because the obvious path is a generic Payment Link. Using it correctly signals familiarity with the actual product surface, not just the marketing page.

---

## 5. Safety & Policy Engine

The language model recommends; it does not directly control money-adjacent actions or unbounded customer contact. Every recommendation passes through a deterministic policy layer before anything is sent or executed.

```
        AI Reasoning Layer
    (diagnosis, tone, message draft)
                │
                ▼
          Policy Engine
                │
      ┌─────────┼─────────┐
      ▼         ▼         ▼
  Cadence    Banned     Escalation
   cap OK?  language?     cap OK?
      │         │         │
      └─────────┼─────────┘
                ▼
            APPROVED
                │
                ▼
          Send / Execute
```

Example policy check, logged in full:

```
Invoice: INV-2291
Proposed action: Send Tier-2 reminder
✓ Days since last contact ≥ cooldown
✓ Reminder count (1) < cap (3)
✓ No active promise-to-pay in effect
✓ No banned phrases in drafted message
✓ Customer not flagged dispute-likely
Result: APPROVED
```

This separation — LLM for reasoning and language, deterministic code for anything that touches money, contact frequency, or compliance — is the single most important architectural decision in the project, and the thing worth explaining first in the architecture walkthrough.

---

## 6. Reliability Engineering

Two details that are cheap to build and disproportionately signal real payment-systems experience:

- **Idempotent webhook handling.** Razorpay delivers webhooks at-least-once, meaning the same `virtual_account.credited` event can arrive more than once. Vasooli deduplicates by event ID before reconciling, so a retried webhook never double-counts recovered revenue or re-triggers an action.
- **Webhook signature verification.** Every incoming webhook is verified against the Razorpay webhook secret (HMAC) before being trusted. Not glamorous, but it's the difference between a toy integration and one that would survive a real security review.

---

## 7. Dashboard

**Overview**

```
┌─────────────────────────────────────────────────┐
│ Vasooli — AI Receivables Recovery                │
├─────────────────────────────────────────────────┤
│ Total Overdue        Recovered This Period       │
│ ₹6,40,000            ₹2,15,000                   │
│                                                   │
│ Recovery Rate         Avg. Days to Recovery       │
│ 47.2%                 9.4 days                    │
├─────────────────────────────────────────────────┤
│ Recovery Queue                                    │
│                                                   │
│ ABC Traders   ₹42,000   Tier 2   Cash-constrained │
│ Nova Retail   ₹18,500   Tier 1   Oversight        │
│ Kiran & Co    ₹75,000   Human    Dispute-likely   │
├─────────────────────────────────────────────────┤
│ Active Promises        Broken Promises            │
│ 4                      1 (flagged)                │
└─────────────────────────────────────────────────┘
```

**Other views:** invoice detail (full timeline: provisioned → reminders → promise → reconciled), promise tracker, and the full audit log.

---

## 8. Data Model

| Entity | Purpose |
|---|---|
| `merchants` | Owning business |
| `customers` | Invoice recipients |
| `invoices` | Amount, due date, status, diagnosed reason |
| `virtual_accounts` | Razorpay VA per invoice, linked `amount_expected` |
| `reminders` | Escalation log — tier, tone, timestamp, channel |
| `promises` | Extracted date/amount, kept/broken status |
| `reconciliation_events` | Raw webhook payloads, dedup key, matched invoice |
| `audit_logs` | Append-only record of every decision and action |

---

## 9. Evaluation

Track 03 explicitly asks for measured recovery, not just a working demo, so Vasooli ships with a synthetic held-out test set of ~100–200 invoices with known ground-truth outcomes (would-pay-anyway / needed-one-nudge / needed-multiple-reminders / would-default), run against the policy to produce:

```
VASOOLI EVALUATION
Test invoices                 150
Total overdue                 ₹18.2L
Recovered (within window)     ₹9.6L
Recovery rate                 52.7%
Avg. days to recovery         8.1
Correct tone selection        84%
False escalations             5.3%   (flagged human, didn't need it)
Missed escalations            3.1%   (should've flagged, didn't)
Automation rate                71%   (resolved without human touch)
```

This is what turns "we built a reminder bot" into "we built and measured a recovery policy" — the exact distinction the track's own bar is drawing.

---

## 10. Tech Architecture

```
                         RAZORPAY
                            │
                ┌───────────┴────────────┐
                │                        │
       Virtual Accounts API     Webhooks (virtual_account.*)
                │                        │
                ▼                        ▼
       ┌─────────────────────────────────────┐
       │           FastAPI Backend             │
       └────────┬───────────────────┬─────────┘
                │                   │
                ▼                   ▼
          PostgreSQL          AI Reasoning Layer
                │             (diagnosis, tone,
                │              message drafting)
                │                   │
                └─────────┬─────────┘
                          ▼
                    Policy Engine
              (cadence caps, compliance,
                promise-pause logic)
                          │
                ┌─────────┴─────────┐
                ▼                   ▼
          Send Reminder        Flag for Human
          (email)                Review
                          │
                          ▼
                Wait for Payment
                          │
                          ▼
             virtual_account.credited
                          │
                          ▼
            Auto-Reconcile + Mark Paid
                          │
                          ▼
                  ₹ Recovered / Dashboard
```

**Backend:** FastAPI (Python)
**Database:** PostgreSQL
**AI:** Google AI Studio — **Gemini 3.7 Flash** (primary) with **Gemini 3.6 Flash** as failover — for reason inference, tone selection rationale, and message drafting. Deterministic Python for every cadence, compliance, and money-matching decision.
**Frontend:** Next.js / React dashboard
**Notification channel:** Email (Resend/SendGrid free tier) — reliably demoable within the build window; WhatsApp is deliberately not used unless API access already exists
**Payments infrastructure:** Razorpay Smart Collect (Virtual Accounts API + Webhooks), test mode

**AI failover:** if the primary model hits an RPM/RPD quota, times out, or returns a server error, the request retries on the fallback model. If both fail, the system falls back to rule-based diagnosis and templated reminder copy — the agent degrades, it never breaks. Every failover is written to the audit log. Model IDs are configuration, not code, so swapping them is a `.env` change.


---

## 11. MVP Scope

**Must-have:**
- Batch ingestion of ~50–100 synthetic overdue invoices
- One real Razorpay test-mode virtual account provisioned per invoice via the API
- Reason inference → tone selection (4 categories, 3 tiers)
- Three-tier escalation cadence with hard caps and cooldowns, sent by email
- Promise-to-pay extraction from replies, with pause/resume logic
- Real webhook consumption (`virtual_account.credited`) → deterministic auto-reconciliation → invoice marked paid
- Idempotent webhook handling (dedup by event ID) + signature verification
- Full audit trail
- Dashboard: overview metrics, recovery queue, promise tracker, audit log
- Evaluation run against a synthetic held-out test set with the metrics in Section 9

**Explicitly out of scope for this build:**
- Real WhatsApp Business API or voice integration (no API access in hand — mock or skip)
- Any other recovery loop (cart abandonment, subscription retry) — this is one loop, done fully, not several done partially
- General multi-source financial reconciliation across a merchant's whole ledger — that's Track 04's problem; Vasooli reconciles only its own recovery queue against its own virtual accounts
- Fraud/dispute detection logic — that's Track 02's problem
- A custom-trained or fine-tuned model — a small LLM call plus rules is enough
- Fully autonomous escalation with no human cap, at any tier

---

## 12. Demo Script (3–5 minutes)

**0:00 – Problem.** "This merchant has ₹6.4L sitting in overdue B2B invoices, chased inconsistently by whoever remembers to."

**0:20 – Queue.** Show the batch: invoices, real virtual accounts provisioned, diagnosed reason per case.

**0:50 – One case, in detail.** Open an invoice: reason inferred ("cash-constrained, moderate confidence"), tone chosen, the actual reminder email sent, a promise-to-pay captured from a reply and logged.

**1:20 – Live reconciliation.** Trigger a simulated incoming payment against a real virtual account from the Razorpay test-mode dashboard, on camera.

**1:40 – The webhook fires live.** `virtual_account.credited` lands, the invoice flips from "chasing" to "recovered" in real time on the dashboard, no refresh — the ₹-recovered counter ticks up.

**2:00 – Broken promise, handled correctly.** Show a case where a promise was broken and escalation resumed automatically, fully logged.

**2:30 – Engineering rigor.** Audit trail, the policy engine's approve/reject log, idempotent webhook handling.

**2:50 – Close.** "Vasooli doesn't just remind customers to pay. It tracks what they commit to, reconciles the moment real money moves, and proves exactly how much revenue came back — on a schedule that's compliant, not just automated."

---

## 13. Positioning

**Name:** Vasooli
**Subtitle:** AI-powered B2B receivables recovery agent for merchants
**Core promise:** Chase. Track. Reconcile. Recover.

Avoid framing it as "an AI reminder bot" or "a WhatsApp collections chatbot" — both undersell the two things that actually differentiate it: the promise-tracking loop, and reconciliation against a real Razorpay primitive most teams won't use.

---

## 14. Roadmap (only after the MVP is solid)

- **Phase 2:** extend the reason-inference model with more historical signal once real usage data exists; add a lightweight WhatsApp channel if API access becomes available
- **Phase 3:** merchant-configurable escalation policy (cadence, tone, caps) instead of hardcoded tiers

Neither is attempted before the core loop — ingestion through reconciliation through audit — is fully working and evaluated end to end.

---

## 15. Final Project Definition

**Vasooli** is an AI-powered receivables recovery agent that ingests a merchant's overdue B2B invoices, diagnoses why each is likely at risk, chases them on a bounded and compliant escalation schedule, tracks and enforces the promises customers make to pay, reconciles real incoming payments the moment Razorpay confirms them, and measures exactly how much revenue was recovered.

**Core loop:** Ingest → Diagnose → Escalate → Track Promises → Reconcile → Measure

**Razorpay's role:** real payment infrastructure — Smart Collect virtual accounts, webhooks, test-mode payment simulation — not a branding layer.

**AI's role:** reasoning, diagnosis, and message drafting — never unrestricted control over money-matching or unbounded customer contact.

**Backend's role:** reliability, idempotency, policy enforcement, and audit.

**Success metric:** ₹ actually recovered, verified against a real payment event, not an assumption.
