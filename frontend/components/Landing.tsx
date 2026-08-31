import type { CSSProperties } from "react";
import Link from "next/link";

const STEPS = [
  ["01", "Connect your financial system.", "Authorize Zoho Books or use a signed custom feed. Vasooli stores normalized, tenant-scoped invoice and customer records and records every synchronization run.", "Zoho Books · signed custom webhook · CSV"],
  ["02", "Detect what needs attention.", "Overdue dates, payment state, active promises, disputes, suppressions, delivery outcomes, and policy limits are evaluated by deterministic application rules.", "Versioned policy · quiet windows · bounded attempts"],
  ["03", "Communicate with context.", "AI helps interpret history and draft clear language. Authoritative invoice facts are inserted and validated by the application before a message can enter the delivery queue.", "Trusted amounts and dates · delivery tracking · bounce suppression"],
  ["04", "Reconcile and stop safely.", "Signed Razorpay events and authenticated provider reads update the payment ledger. External payments can be recorded by an authorized operator with an explicit audit classification.", "Replay-safe events · partial payments · automatic recovery stop"],
];

const TRACE = [
  ["09:00", "Invoice synchronized", "ERP record normalized and scoped to the workspace"],
  ["09:01", "Policy evaluated", "Invoice eligible for the first recovery step"],
  ["09:02", "Reminder accepted", "Provider message ID retained for delivery events"],
  ["14:42", "Customer reply received", "Sender and inbound webhook verified"],
  ["14:43", "Dispute opened", "Automation paused for operator review"],
  ["11:18", "Payment reconciled", "Trusted financial event updates the ledger"],
  ["11:19", "Recovery stopped", "Pending contact cancelled and action audited"],
];

function Arrow() { return <span aria-hidden>↗</span>; }

export function Landing() {
  return (
    <div className="landing-shell">
      <div className="landing-progress" aria-hidden />
      <section className="landing-hero landing-grid" aria-labelledby="landing-title">
        <div className="landing-glow" aria-hidden />
        <div className="landing-kicker landing-reveal" data-reveal><span className="landing-pulse" />Automated B2B receivables recovery</div>
        <h1 id="landing-title" className="landing-display landing-reveal" data-reveal>Stop when they dispute.<br /><span>Close when money lands.</span></h1>
        <div className="landing-hero-copy landing-reveal" data-reveal>
          <p>Vasooli pauses when a customer promises or disputes, keeps follow-up inside a bounded policy, and closes recovery when trusted financial records confirm the money. Your team sees the reason, owner, and next action throughout.</p>
          {/* Three audiences reach this page: a merchant who wants an account, a merchant
          who already has one, with a seeded read-only demo available separately.
          The third had no route in at all for a while — every link led to register or
          sign-in — which made a working product look like a locked door. */}
          <div className="landing-actions"><Link href="/register" className="landing-button landing-button-primary">Start your workspace <Arrow /></Link><Link href="/login" className="landing-button landing-button-quiet">Open the demo — no sign-up</Link><Link href="/live/login" className="landing-button landing-button-quiet">Sign in</Link></div>
        </div>
        <div className="landing-proof-strip landing-reveal" data-reveal><span>Tenant-scoped records</span><span>ERP synchronization</span><span>Razorpay reconciliation</span><span>Audited decisions</span></div>
        <a href="#how" className="landing-scroll-cue"><span>Follow the recovery loop</span><i aria-hidden /></a>
      </section>

      <section className="landing-statement landing-grid" aria-labelledby="problem-title">
        <span className="landing-section-number landing-reveal" data-reveal>00</span>
        <div className="landing-statement-copy"><p className="landing-eyebrow landing-reveal" data-reveal>The operating problem</p><h2 id="problem-title" className="landing-big-copy landing-reveal" data-reveal>A reminder is easy.<br /><em>A trustworthy recovery loop is not.</em></h2><p className="landing-explainer landing-reveal" data-reveal>Finance teams need current invoice state, consistent follow-up, clear ownership of disputes, and proof of why contact stopped. Vasooli brings those steps into one controlled workflow without making AI the source of financial truth.</p></div>
      </section>

      <section id="how" className="landing-story landing-grid" aria-labelledby="how-title">
        <div className="landing-story-heading"><span className="landing-section-number landing-reveal" data-reveal>01</span><div><p className="landing-eyebrow landing-reveal" data-reveal>How Vasooli works</p><h2 id="how-title" className="landing-section-title landing-reveal" data-reveal>From ERP record<br />to resolved balance.</h2></div></div>
        <ol className="landing-steps">{STEPS.map(([number, title, body, detail]) => <li key={number} className="landing-step landing-reveal" data-reveal><span className="landing-step-number">{number}</span><div><h3>{title}</h3><p>{body}</p><span className="landing-step-detail">{detail}</span></div></li>)}</ol>
      </section>

      <section id="integrations" className="landing-trace-section landing-grid" aria-labelledby="integration-title">
        <div className="landing-trace-copy"><span className="landing-section-number landing-reveal" data-reveal>02</span><p className="landing-eyebrow landing-reveal" data-reveal>Continuous financial context</p><h2 id="integration-title" className="landing-section-title landing-reveal" data-reveal>Connect once.<br />Keep the ledger current.</h2><p className="landing-explainer landing-reveal" data-reveal>Provider-neutral adapters normalize source invoices into canonical records. Cursor state, source versions, replay protection, synchronization history, failure details, and freshness deadlines remain visible to operators.</p></div>
        <div className="landing-boundary landing-reveal" data-reveal><div className="landing-boundary-row"><span>Zoho Books</span><strong>OAuth-based read connection</strong><small>Organization-scoped invoice retrieval</small></div><div className="landing-boundary-row"><span>CSV</span><strong>Previewed ledger import</strong><small>Row-level validation and duplicate reporting</small></div><div className="landing-boundary-row landing-boundary-final"><span>Custom ERP</span><strong>Signed webhook ingestion</strong><small>Event identity and duplicate protection</small></div></div>
      </section>

      <section id="safety" className="landing-safety landing-grid" aria-labelledby="safety-title">
        <div className="landing-story-heading"><span className="landing-section-number landing-reveal" data-reveal>03</span><div><p className="landing-eyebrow landing-reveal" data-reveal>Safety architecture</p><h2 id="safety-title" className="landing-section-title landing-reveal" data-reveal>AI assists.<br />Policy authorizes.<br />Financial events settle.</h2></div></div>
        <div className="landing-boundary landing-reveal" data-reveal><div className="landing-boundary-row"><span>Intelligence</span><strong>Classifies replies and drafts language</strong><small>Structured output, timeouts, fallbacks, and audited model metadata</small></div><div className="landing-boundary-row"><span>Control</span><strong>Decides whether communication is allowed</strong><small>Cooldowns, attempt caps, promises, disputes, suppressions, and pauses</small></div><div className="landing-boundary-row landing-boundary-final"><span>Financial truth</span><strong>Changes balances from trusted records</strong><small>Signed webhooks, authenticated reads, and attributed operator entries</small></div><p className="landing-boundary-note">Customer text and model output can never mark an invoice paid.</p></div>
      </section>

      <section id="operations" className="landing-trace-section landing-grid" aria-labelledby="operations-title">
        <div className="landing-trace-copy"><span className="landing-section-number landing-reveal" data-reveal>04</span><p className="landing-eyebrow landing-reveal" data-reveal>Conversation and operations</p><h2 id="operations-title" className="landing-section-title landing-reveal" data-reveal>See what happened.<br />Know what happens next.</h2><p className="landing-explainer landing-reveal" data-reveal>A single operational trail connects synchronized invoices, policy decisions, outbound status, verified replies, promises, disputes, operator actions, payment events, and recovery closure.</p></div>
        <div className="landing-trace landing-reveal" data-reveal aria-label="Illustrative recovery workflow"><div className="landing-trace-head"><span>RECOVERY WORKFLOW</span><span className="landing-status"><i /> AUDITED</span></div><ol>{TRACE.map(([time, event, detail], index) => <li key={`${time}-${event}`} style={{ "--trace-index": index } as CSSProperties}><time>{time}</time><span className="landing-trace-dot" /><div><strong>{event}</strong><small>{detail}</small></div></li>)}</ol></div>
      </section>

      <section className="landing-proof landing-grid" aria-labelledby="control-title">
        <div className="landing-story-heading"><span className="landing-section-number landing-reveal" data-reveal>05</span><div><p className="landing-eyebrow landing-reveal" data-reveal>Operational controls</p><h2 id="control-title" className="landing-section-title landing-reveal" data-reveal>Built for accountable teams.</h2></div></div>
        <div className="landing-metrics"><div className="landing-metric landing-reveal" data-reveal><strong>Scoped</strong><span>Workspace access</span><small>Role permissions and tenant-bound data access</small></div><div className="landing-metric landing-reveal" data-reveal><strong>Replay-safe</strong><span>Integration events</span><small>Provider event identities and idempotent reconciliation</small></div><div className="landing-metric landing-reveal" data-reveal><strong>Observable</strong><span>Background operations</span><small>Run history, retries, exceptions, and stale-connection signals</small></div></div>
        <p className="landing-proof-note landing-reveal" data-reveal>Payment state is derived from integer minor-unit records. Sensitive connector secrets are encrypted at rest, and high-impact actions require explicit permission with recent re-authentication where configured.</p>
      </section>

      <section className="landing-final landing-grid"><p className="landing-eyebrow landing-reveal" data-reveal>Receivables recovery with financial discipline</p><h2 className="landing-final-title landing-reveal" data-reveal>Stop when they dispute.<br />Pause when they promise.<br />Close when money lands.</h2><div className="landing-actions landing-reveal" data-reveal><Link href="/register" className="landing-button landing-button-primary">Start your workspace <Arrow /></Link><Link href="/login" className="landing-button landing-button-quiet">Open the demo</Link><Link href="/pricing" className="landing-button landing-button-quiet">View plans</Link></div></section>
    </div>
  );
}
