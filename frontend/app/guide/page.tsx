import Link from "next/link";

import { API_BASE } from "@/lib/api";

/**
 * The page a reviewer lands on when nobody is there to narrate.
 *
 * Public on purpose — it is listed in the proxy's isPublic allowlist. Before this
 * existed, an unaccompanied visitor hit a password field and learned nothing: not
 * what the product does, not that it is a hackathon build, not that the interesting
 * behaviour is two clicks away behind a login they do not have.
 *
 * It carries no credentials, and it no longer needs to. Where the deployment enables
 * it, the login page offers a read-only reviewer session on the `auditor` role, which
 * cannot write anything — so a reviewer with nobody to ask can still see the real
 * dashboard over the seeded demo ledger. A shared password handed out per reviewer was
 * the previous
 * answer, and it is a worse one: it is a real credential sitting in somebody's inbox.
 */

export const metadata = {
  title: "Reviewer guide — Vasooli",
  description: "What Vasooli is, what is real, and where to click first.",
};

const REPO = "https://github.com/aryan-nmaurya/Vasooli";

/**
 * What this deployment actually offers, and how big the seeded ledger is.
 *
 * Both were hard-coded before. The demo controls were described unconditionally, so
 * where they are switched off — production, deliberately — the guide walked an
 * unaccompanied judge to a button that does nothing. The invoice count was the word
 * "eight", which drifts the moment the seed changes.
 *
 * Never throws: this page is the fallback for a reviewer who has nobody to ask, so a
 * backend blip must degrade it, not blank it.
 */
async function deploymentFacts() {
  const modes = await fetch(`${API_BASE}/api/auth/modes`, { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  return {
    demoControls: Boolean(modes?.demo_controls),
    invoiceCount:
      typeof modes?.demo_invoice_count === "number"
        ? (modes.demo_invoice_count as number)
        : null,
  };
}

function Section({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-col gap-1 border-b border-line pb-2.5">
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-4">
          {eyebrow}
        </span>
        <h2 className="text-lg font-semibold text-ink">{title}</h2>
      </div>
      {children}
    </section>
  );
}

export default async function GuidePage() {
  const { demoControls, invoiceCount } = await deploymentFacts();
  const ledgerLabel =
    invoiceCount === null
      ? "Seeded demo invoices, not real merchants"
      : `${invoiceCount} seeded demo ${invoiceCount === 1 ? "invoice" : "invoices"}, not real merchants`;

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-9 px-3 py-4 sm:px-0">
      <header className="flex flex-col gap-3">
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-4">
          For reviewers and mentors
        </span>
        <h1 className="text-2xl font-semibold tracking-tight text-ink">
          Start here
        </h1>
        <p className="text-[15px] leading-relaxed text-ink-2">
          Vasooli chases overdue B2B invoices on a bounded, auditable schedule.
          It works out <em>why</em> each invoice is unpaid, writes a reminder in
          a tone that matches, stops the moment a customer promises to pay or
          disputes the bill, and stops permanently the moment money actually
          arrives — confirmed by Razorpay itself — a signed webhook, or an
          authenticated reply to a call we made — never by a model&apos;s
          opinion.
        </p>
      </header>

      <Section
        eyebrow="The claim worth testing"
        title="The AI cannot touch money"
      >
        <p className="text-sm leading-relaxed text-ink-2">
          A language model reads customer replies and drafts reminder copy. It
          cannot mark an invoice paid, change an amount, pause recovery, or send
          anything — not because the prompt asks it not to, but because the code
          it would need is unreachable from the AI layer. That boundary is
          enforced by a test that parses the import graph and fails the build if
          it is ever crossed.
        </p>
        <div className="rounded-lg border border-line bg-panel px-4 py-3">
          <div className="text-xs uppercase tracking-wider text-ink-3">
            Where to verify it
          </div>
          <ul className="mt-2 flex flex-col gap-1.5 font-mono text-[12px] text-ink-2">
            <li>backend/tests/architecture/test_layering.py</li>
            <li>backend/tests/integration/test_disputes.py</li>
            <li>backend/app/policy/disputes.py</li>
          </ul>
          <p className="mt-2.5 text-xs leading-relaxed text-ink-3">
            The first proves the AI layer cannot import a mailer, a database
            session, or the payment client. The second asserts that when
            recovery pauses, the pause is attributed to the policy engine and
            the <em>reading</em> to the AI — two separate actors in the audit
            trail. The third is the decision itself: a pure function, no model
            involved.
          </p>
        </div>
      </Section>

      <Section eyebrow="Honesty" title="What is real, and what is not">
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full min-w-[30rem] text-sm">
            <tbody className="divide-y divide-line-2">
              {[
                [
                  "Payment links, reconciliation, webhooks",
                  "Real Razorpay — test mode",
                ],
                [
                  "Reminder emails",
                  "Real, sent through Resend from this domain",
                ],
                ["Customer replies", "Real inbound email, signature-verified"],
                [
                  "Reason diagnosis, reply reading, drafting",
                  "Real Gemini, with deterministic fallbacks",
                ],
                [
                  "Recipients",
                  "Redirected to the operator inbox, not customers",
                ],
                ["The ledger", ledgerLabel],
              ].map(([what, state]) => (
                <tr key={what}>
                  <td className="px-4 py-2.5 text-ink">{what}</td>
                  <td className="px-4 py-2.5 text-right text-ink-3">{state}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs leading-relaxed text-ink-3">
          The amber bar at the top of every dashboard page reports these modes
          live. It turns red if the system is ever pointed at real customers or
          live payment keys.
        </p>
      </Section>

      <Section eyebrow="Two minutes" title="Where to click">
        <ol className="flex flex-col gap-3">
          {[
            [
              "Open the recovery queue",
              "Every row answers “why is this happening?” in one sentence — no need to open anything to understand the state of the ledger.",
            ],
            [
              "Open the invoice marked Disputed",
              "This is the one worth seeing. Recovery is paused, and the card shows the customer’s own words beside the AI’s reading of them, the claims it extracted, and how confident it was.",
            ],
            [
              "Scroll to the conversation",
              "Every reminder, reply, AI reading, policy decision and payment event in order — colour-coded by who acted.",
            ],
            [
              "Open the audit log",
              "Append-only, enforced by a database trigger rather than convention. Nothing in the application can edit or delete a row.",
            ],
            [
              "Take the ledger with you",
              "Export gives you the queue, the recovered invoices, or the summary as CSV, Excel or PDF — and honours whatever filters are on screen. Import reads a CSV back in, showing you what would happen, with line numbers for anything it cannot parse, before it writes a single row.",
            ],
          ].map(([title, body], i) => (
            <li key={title} className="flex gap-3">
              <span className="mt-0.5 font-mono text-sm font-semibold text-accent">
                {i + 1}
              </span>
              <div className="flex flex-col gap-0.5">
                <span className="text-sm font-medium text-ink">{title}</span>
                <span className="text-sm leading-relaxed text-ink-2">
                  {body}
                </span>
              </div>
            </li>
          ))}
        </ol>
        <p className="rounded-lg border border-line bg-panel px-4 py-3 text-xs leading-relaxed text-ink-3">
          <strong className="text-ink-2">Safe to explore.</strong> Reminder mail
          is redirected away from customers and cannot be pointed at them,
          Razorpay is in test mode, and every action you take is attributed to
          you in the audit log. If you would rather decide without sending
          anything, the grey <strong className="text-ink-2">Dry run</strong>{" "}
          button evaluates the whole cadence and sends nothing.
        </p>
      </Section>

      {/*
        Rendered only where the demo controls are actually on. Both cards below drive
        them, and they are deliberately off in production — so unconditionally this
        section walked an unaccompanied judge to a button that does nothing. Kept
        conditional rather than deleted: it returns by itself wherever the clock is
        enabled, and cannot rot back into a lie.
      */}
      {demoControls ? (
        <Section
          eyebrow="Try it yourself"
          title="Compress three weeks into two minutes"
        >
          <p className="text-sm leading-relaxed text-ink-2">
            Reminders fire at 3, 10 and 21 days overdue, and mail is redirected
            away from customers. Both are right for a real merchant and
            impossible to evaluate in a sitting, so the{" "}
            <strong className="text-ink">Workspace settings</strong> page lets
            you move past both without covering the dashboard.
          </p>
          <div className="flex flex-col gap-2.5">
            <div className="rounded-lg border border-line bg-panel px-4 py-3">
              <div className="text-xs font-medium uppercase tracking-wider text-ink-3">
                Time machine
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-ink-2">
                Move the clock forward and the ordinary recovery cycle runs
                against the later date. Nothing is fabricated — the policy
                engine still decides what is due. Auto-play walks the whole
                cadence while you watch. Every move is in the audit log, and the
                panel shows the simulated and real dates side by side.
              </p>
            </div>
            <div className="rounded-lg border border-line bg-panel px-4 py-3">
              <div className="text-xs font-medium uppercase tracking-wider text-ink-3">
                Send reminders to
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-ink-2">
                Put your own address in and run a cycle. A real reminder
                arrives; reply to it and your reply travels the live inbound
                path — signed webhook, sender correlation, dispute detection —
                exactly as a customer&apos;s would. This only moves the
                redirect: it cannot switch it off, so the seeded ledger&apos;s
                invented addresses are never contacted.
              </p>
            </div>
          </div>
        </Section>
      ) : null}

      <Section
        eyebrow="Measured, not asserted"
        title="How well it actually works"
      >
        <p className="text-sm leading-relaxed text-ink-2">
          An evaluation harness runs three strategies over 150 invoices and 45
          simulated days: no chasing, a naive chaser, and Vasooli.
        </p>
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full min-w-[26rem] text-sm">
            <thead>
              <tr className="border-b border-line text-left">
                <th className="px-4 py-2 text-xs font-medium uppercase tracking-wider text-ink-3">
                  &nbsp;
                </th>
                <th className="px-4 py-2 text-right text-xs font-medium uppercase tracking-wider text-ink-3">
                  Naive
                </th>
                <th className="px-4 py-2 text-right text-xs font-medium uppercase tracking-wider text-ink-3">
                  Vasooli
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line-2 tabular-nums">
              <tr>
                <td className="px-4 py-2.5 text-ink">Recovered, by value</td>
                <td className="px-4 py-2.5 text-right text-ink-2">85.0%</td>
                <td className="px-4 py-2.5 text-right font-medium text-ink">
                  65.1%
                </td>
              </tr>
              <tr>
                <td className="px-4 py-2.5 text-ink">Contacts per invoice</td>
                <td className="px-4 py-2.5 text-right text-ink-2">5.17</td>
                <td className="px-4 py-2.5 text-right font-medium text-ink">
                  1.10
                </td>
              </tr>
              <tr>
                <td className="px-4 py-2.5 text-ink">Compliance breaches</td>
                <td className="px-4 py-2.5 text-right text-ink-2">92</td>
                <td className="px-4 py-2.5 text-right font-medium text-ink">
                  0
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="text-sm leading-relaxed text-ink-2">
          The naive chaser recovers more. It does so by contacting every
          customer five times and never stopping — including customers who
          already paid and customers disputing the bill. Vasooli recovers 77% of
          that with a fifth of the contacts and no breaches of its own rules.
          The claim is not &ldquo;recovers the most&rdquo;; it is
          &ldquo;recovers most of it without behaviour you would be embarrassed
          to defend.&rdquo;
        </p>
      </Section>

      <Section eyebrow="Scope" title="What this is not">
        <p className="text-sm leading-relaxed text-ink-2">
          The ledger you are shown is seeded, and its customers are invented —
          no real buyer is ever contacted from it. What surrounds that ledger is
          not a mock: the system is multi-tenant, with per-merchant isolation
          enforced by the database itself rather than by application filters,
          and it carries live subscription billing on three plans with real
          Autopay mandates.
        </p>
        <p className="text-sm leading-relaxed text-ink-2">
          What it has not done is run a large book for a long time. There is no
          operational history here, no scale evidence, and no third-party audit
          — and what production would still require is written up in the
          repository rather than implied here.
        </p>
        <div className="flex flex-wrap gap-2">
          <a
            href={REPO}
            target="_blank"
            rel="noreferrer"
            className="rounded-md bg-invert px-3 py-1.5 text-sm font-medium text-invert-ink transition hover:opacity-90"
          >
            Read the source
          </a>
          <Link
            href="/login"
            className="rounded-md px-3 py-1.5 text-sm text-ink-2 ring-1 ring-inset ring-line transition hover:bg-panel-2 hover:text-ink"
          >
            Open the dashboard
          </Link>
        </div>
        <p className="mt-3 text-xs text-ink-3">
          If this deployment has reviewer access enabled, the login page offers
          &ldquo;Continue as reviewer&rdquo; — a read-only session over the
          seeded demo ledger. The workflows are real; the customers are
          synthetic and the money is Razorpay test mode. Every write is refused
          for that session, so nothing you click can send anyone an email or
          change what the system believes it is owed.
        </p>
      </Section>
    </div>
  );
}
