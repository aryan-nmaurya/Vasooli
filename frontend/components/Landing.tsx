import type { CSSProperties } from "react";
import Link from "next/link";

const REPO = "https://github.com/aryan-nmaurya/Vasooli";

const STEPS = [
  ["01", "Find what needs attention.", "Vasooli finds overdue Razorpay invoices and reads the complete history before it acts. No spreadsheet triage. No invoice forgotten in a tab.", "Invoice age · contact history · promises · disputes · payment state"],
  ["02", "Choose the next safe move.", "Deterministic policy decides whether to wait, follow up, pause, or escalate. AI understands context and drafts language; it cannot override the rules.", "10 policy checks · 7-day cooldown · 3-contact limit"],
  ["03", "Listen before chasing again.", "A promise to pay pauses recovery until the promised date. A dispute stops automation and opens a case for a person. Silence follows a bounded schedule.", "Promise tracking · dispute handoff · inbound reply classification"],
  ["04", "Stop when the money lands.", "Only verified Razorpay state can mark an invoice paid. The workflow closes immediately, the audit trail remains, and the customer is not contacted again.", "Signed webhooks · authenticated reconciliation · immediate stop"],
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
            Vasooli follows up on overdue invoices, understands replies, tracks
            promises, and stops the instant Razorpay confirms payment.
          </p>
          <div className="landing-actions">
            <Link href="/login" className="landing-button landing-button-primary">
              Open the live demo <Arrow />
            </Link>
            <a href="#how" className="landing-button landing-button-quiet">
              See how it works ↓
            </a>
          </div>
        </div>
        <div className="landing-proof-strip landing-reveal" data-reveal>
          <span>Built on Razorpay test mode</span>
          <span>Every action audited</span>
          <span>Bounded by policy</span>
          <span>Human escalation included</span>
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
            <strong>Provides the only source of payment truth</strong>
            <small>Signed webhooks and authenticated Razorpay reads</small>
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
        <div className="landing-metrics">
          <div className="landing-metric landing-reveal" data-reveal>
            <strong>65.1%</strong><span>invoice value recovered</span><small>Across a 150-invoice, 45-day simulation</small>
          </div>
          <div className="landing-metric landing-reveal" data-reveal>
            <strong>1.10</strong><span>contacts per invoice</span><small>Compared with 5.17 for a naive chaser</small>
          </div>
          <div className="landing-metric landing-reveal" data-reveal>
            <strong>0</strong><span>policy breaches</span><small>Compared with 92 for a naive chaser</small>
          </div>
        </div>
        <p className="landing-proof-note landing-reveal" data-reveal>
          A naive chaser recovered more by contacting everyone repeatedly and never
          stopping. Vasooli recovered most of that value with one-fifth of the contact
          and none of the behaviour a business would be embarrassed to defend.
        </p>
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
            links, outbound email, inbound replies, AI calls, stopping rules, and audit
            trail are implemented. No real customer money has moved through it.
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
