import type { CSSProperties } from "react";
import Link from "next/link";

const REPO = "https://github.com/aryan-nmaurya/Vasooli";

// Wording here is held to what the code actually does. The temptation on a landing
// page is to describe the product you intend; the cost is a reviewer opening the
// repository and finding a CSV importer behind the word "finds".
const STEPS = [
  ["01", "Bring the ledger in.", "You import your overdue invoices — a CSV, or the API. Vasooli creates a Razorpay payment link for each one and reads the history that came with it. It does not browse your Razorpay account or discover invoices on its own.", "CSV or API import · one payment link per invoice · history from the import"],
  ["02", "Choose the next safe move.", "Deterministic policy decides whether to wait, follow up, pause, or escalate. AI understands context and drafts language; it cannot override the rules or move money.", "10 policy checks · 7-day cooldown · 3-contact limit"],
  ["03", "Listen before chasing again.", "A promise to pay pauses recovery until the promised date. A dispute stops automation and opens a case for a person. A reminder that hard bounces stops the cadence instead of advancing it.", "Promise tracking · dispute handoff · delivery and bounce events"],
  ["04", "Stop when the money lands — however it lands.", "A signed Razorpay webhook settles an invoice automatically. A bank transfer, UPI, or cheque is recorded by an operator and settles it the same way. Either closes the payment link and ends the chase.", "Signed webhooks · hourly Razorpay sync · hand-recorded bank payments"],
];

// Bare times with no date read as same-day and in order — which the original data
// was not: payment landed at 11:18 on the promised Friday, a day after the 14:42
// entries on the Monday it was reported due. Rendered without a day label, that put
// the "payment verified" row above two entries that happened later the same
// afternoon, in a section whose entire point is auditable chronological evidence.
const TRACE = [
  ["Mon 09:00", "Invoice detected", "INV-2048 · 8 days overdue"],
  ["Mon 09:00", "Policy approved", "First reminder · professional tone"],
  ["Mon 09:01", "Email delivered", "Contact 1 of 3"],
  ["Mon 14:42", "Reply understood", "Promise to pay · Friday"],
  ["Mon 14:42", "Recovery paused", "No contact before 28 Aug"],
  ["Fri 11:18", "Payment verified", "Razorpay webhook · ₹84,000"],
  ["Fri 11:18", "Payment link closed", "No second payment possible"],
];

function Arrow() {
  return <span aria-hidden>↗</span>;
}

export function Landing() {
  return (
    <div className="landing-shell">
      <div className="landing-progress" aria-hidden />
      <section className="landing-hero landing-grid" aria-labelledby="landing-title">
        <div className="landing-glow" aria-hidden />
        <div className="landing-kicker landing-reveal" data-reveal>
          <span className="landing-pulse" />
          AI receivables recovery for Indian businesses
        </div>
        <h1 id="landing-title" className="landing-display landing-reveal" data-reveal>
          Recover revenue.
          <br />
          <span>Keep the relationship.</span>
        </h1>
        <div className="landing-hero-copy landing-reveal" data-reveal>
          <p>
            Import your overdue ledger. Vasooli follows up, understands the replies,
            tracks what customers promise, and stops as soon as the payment is
            confirmed — by Razorpay, or by a bank transfer you record.
          </p>
          <div className="landing-actions">
            <Link href="/login" className="landing-button landing-button-primary">
              Open the read-only demo <Arrow />
            </Link>
            <a href="#how" className="landing-button landing-button-quiet">
              See how it works ↓
            </a>
          </div>
        </div>
        <div className="landing-proof-strip landing-reveal" data-reveal>
          <span>Razorpay test mode · no real money</span>
          <span>Single merchant</span>
          <span>Every action audited</span>
          <span>Bounded by policy</span>
        </div>
        <div className="landing-scroll-cue" aria-hidden>
          <span>Scroll to follow the recovery loop</span>
          <i />
        </div>
      </section>

      <section className="landing-statement landing-grid" aria-labelledby="problem-title">
        <span className="landing-section-number landing-reveal" data-reveal>00</span>
        <div className="landing-statement-copy">
          <p className="landing-eyebrow landing-reveal" data-reveal>The real problem</p>
          <h2 id="problem-title" className="landing-big-copy landing-reveal" data-reveal>
            Sending a reminder is easy.
            <br />
            <em>Knowing when to stop is the product.</em>
          </h2>
          <p className="landing-explainer landing-reveal" data-reveal>
            Good recovery means remembering who promised what, recognising a genuine
            dispute, giving customers room to respond, and never chasing an invoice
            that has already settled. A recurring email job cannot do that. Vasooli
            closes the whole loop.
          </p>
        </div>
      </section>

      <section id="how" className="landing-story landing-grid" aria-labelledby="how-title">
        <div className="landing-story-heading">
          <span className="landing-section-number landing-reveal" data-reveal>01</span>
          <div>
            <p className="landing-eyebrow landing-reveal" data-reveal>One closed loop</p>
            <h2 id="how-title" className="landing-section-title landing-reveal" data-reveal>
              From overdue
              <br />
              to resolved.
            </h2>
          </div>
        </div>
        <ol className="landing-steps">
          {STEPS.map(([number, title, body, detail]) => (
            <li key={number} className="landing-step landing-reveal" data-reveal>
              <span className="landing-step-number">{number}</span>
              <div>
                <h3>{title}</h3>
                <p>{body}</p>
                <span className="landing-step-detail">{detail}</span>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section id="safety" className="landing-safety landing-grid" aria-labelledby="safety-title">
        <div className="landing-story-heading">
          <span className="landing-section-number landing-reveal" data-reveal>02</span>
          <div>
            <p className="landing-eyebrow landing-reveal" data-reveal>Safe by architecture</p>
            <h2 id="safety-title" className="landing-section-title landing-reveal" data-reveal>
              AI reads.
              <br />
              Policy decides.
              <br />
              Razorpay verifies.
            </h2>
          </div>
        </div>

        <div className="landing-boundary landing-reveal" data-reveal>
          <div className="landing-boundary-row">
            <span>AI layer</span>
            <strong>Understands replies and drafts language</strong>
            <small>Cannot send, settle, or access payment credentials</small>
          </div>
          <div className="landing-boundary-row">
            <span>Policy layer</span>
            <strong>Authorises every recovery action</strong>
            <small>Cooldowns, limits, dispute stops, and promise holds</small>
          </div>
          <div className="landing-boundary-row landing-boundary-final">
            <span>Payment layer</span>
            <strong>Decides what has actually been paid</strong>
            <small>
              Signed webhooks and authenticated Razorpay reads. A payment made outside a
              Vasooli link is recorded by a named operator and marked as their
              assertion, never as verified provider truth.
            </small>
          </div>
          <p className="landing-boundary-note">
            The separation is enforced in code and tested against the import graph—not
            left as a promise inside an AI prompt.
          </p>
        </div>
      </section>

      <section className="landing-trace-section landing-grid" aria-labelledby="trace-title">
        <div className="landing-trace-copy">
          <span className="landing-section-number landing-reveal" data-reveal>03</span>
          <p className="landing-eyebrow landing-reveal" data-reveal>Every decision leaves evidence</p>
          <h2 id="trace-title" className="landing-section-title landing-reveal" data-reveal>
            See the recovery,
            <br />
            not just the result.
          </h2>
          <p className="landing-explainer landing-reveal" data-reveal>
            Operators can see why an invoice was contacted, what the customer said,
            which rule fired, and exactly what confirmed payment.
          </p>
        </div>
        <div className="landing-trace landing-reveal" data-reveal aria-label="Example recovery trace">
          <div className="landing-trace-head">
            <span>EXAMPLE RECOVERY TRACE</span>
            <span className="landing-status"><i /> BOUNDED</span>
          </div>
          <ol>
            {TRACE.map(([time, event, detail], index) => (
              <li key={`${time}-${event}`} style={{ "--trace-index": index } as CSSProperties}>
                <time>{time}</time>
                <span className="landing-trace-dot" />
                <div>
                  <strong>{event}</strong>
                  <small>{detail}</small>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section id="proof" className="landing-proof landing-grid" aria-labelledby="proof-title">
        <div className="landing-story-heading">
          <span className="landing-section-number landing-reveal" data-reveal>04</span>
          <div>
            <p className="landing-eyebrow landing-reveal" data-reveal>Measured, not asserted</p>
            <h2 id="proof-title" className="landing-section-title landing-reveal" data-reveal>
              Recovery with
              <br />
              restraint.
            </h2>
          </div>
        </div>
        {/* Verified facts first, simulated ones second and labelled as such.
            The recovery figure comes from a simulator in this repository, not from a
            merchant — leading with it invites a reviewer to treat every other number
            as equally soft, which none of them are. */}
        <div className="landing-metrics">
          <div className="landing-metric landing-reveal" data-reveal>
            <strong>943</strong><span>tests passing</span><small>836 backend, 107 frontend. Run them yourself.</small>
          </div>
          <div className="landing-metric landing-reveal" data-reveal>
            <strong>10</strong><span>policy checks before any send</span><small>Pure functions, no model involved</small>
          </div>
          <div className="landing-metric landing-reveal" data-reveal>
            <strong>0</strong><span>ways for AI to settle an invoice</span><small>Enforced in code, tested against the import graph</small>
          </div>
        </div>

        <div className="landing-simulated landing-reveal" data-reveal>
          <p className="landing-eyebrow">Simulated, not observed</p>
          <p className="landing-explainer">
            Against a 150-invoice, 45-day scenario generated by the simulator in this
            repository, Vasooli recovered <strong>65.1%</strong> of invoice value with{" "}
            <strong>1.10</strong> contacts per invoice and no policy breach, where a
            naive chaser needed 5.17 contacts and broke policy 92 times. It also handed{" "}
            <strong>83 of the 150</strong> invoices to a human.
          </p>
          <p className="landing-explainer">
            No merchant has run on this. These are the numbers a deterministic model
            produced about itself, and they belong in the same sentence as that
            caveat — not on a slide on their own.
          </p>
        </div>
      </section>

      <section className="landing-scope landing-grid" aria-labelledby="scope-title">
        <span className="landing-section-number landing-reveal" data-reveal>05</span>
        <div>
          <p className="landing-eyebrow landing-reveal" data-reveal>Honest scope</p>
          <h2 id="scope-title" className="landing-section-title landing-reveal" data-reveal>
            Real workflows.
            <br />
            Test money.
          </h2>
          <p className="landing-explainer landing-reveal" data-reveal>
            Vasooli is a single-merchant system using Razorpay test keys. The payment
            links, outbound email, delivery and bounce events, inbound replies, AI
            calls, stopping rules, and audit trail are implemented and tested. No real
            customer money has moved through it.
          </p>
          <p className="landing-explainer landing-reveal" data-reveal>
            What it is not: it has no connector to your accounting system, so invoices
            are imported rather than discovered and a change in your books does not
            reach it. It does not ingest Razorpay refunds or chargebacks. One merchant,
            one currency. Calling this production-grade would be a stretch; it is a
            production-shaped prototype with the money paths built properly.
          </p>
          <a className="landing-text-link landing-reveal" data-reveal href={REPO} target="_blank" rel="noreferrer">
            Inspect the source and tests <Arrow />
          </a>
        </div>
      </section>

      <section className="landing-final landing-grid">
        <p className="landing-eyebrow landing-reveal" data-reveal>Chase less. Recover better.</p>
        <h2 className="landing-final-title landing-reveal" data-reveal>
          Put every overdue rupee
          <br />
          on a safe path home.
        </h2>
        <div className="landing-actions landing-reveal" data-reveal>
          <Link href="/login" className="landing-button landing-button-primary">
            Explore the dashboard <Arrow />
          </Link>
          <Link href="/guide" className="landing-button landing-button-quiet">
            Read the reviewer guide
          </Link>
        </div>
      </section>
    </div>
  );
}
