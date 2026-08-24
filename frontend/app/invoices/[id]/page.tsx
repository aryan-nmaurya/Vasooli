import Link from "next/link";
import { notFound } from "next/navigation";

import { ProvenanceBadge, ReasonBadge, StatusBadge } from "@/components/badges";
import { Conversation } from "@/components/Conversation";
import { DisputeCard } from "@/components/DisputeCard";
import { PolicyCard } from "@/components/PolicyCard";
import { WhyCard } from "@/components/WhyCard";
import { ProvisionButton } from "@/components/ProvisionButton";
import { SimulateReply } from "@/components/SimulateReply";
import { getInvoice } from "@/lib/api";

export const dynamic = "force-dynamic";

function time(iso: string) {
  return new Date(iso).toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let invoice;
  try {
    invoice = await getInvoice(id);
  } catch {
    notFound();
  }

  return (
    <div className="space-y-6">
      <div>
        <Link href="/" className="text-xs text-ink-3 hover:text-ink-2">
          ← Recovery queue
        </Link>
        <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-2">
          <h1 className="font-mono text-xl font-semibold text-ink">
            {invoice.invoice_number}
          </h1>
          <span className="text-ink-2">{invoice.customer_name}</span>
          <StatusBadge status={invoice.status} />
          <ReasonBadge reason={invoice.reason_category} />
        </div>
      </div>

      <WhyCard
        why={invoice.why}
        next={invoice.why_next}
        state={invoice.why_state}
      />

      {invoice.dispute ? <DisputeCard dispute={invoice.dispute} /> : null}

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          ["Amount", invoice.amount_display],
          ["Paid", invoice.paid_display],
          ["Outstanding", invoice.outstanding_display],
          ["Days overdue", `${invoice.days_overdue}d`],
        ].map(([label, value]) => (
          <div key={label} className="rounded-xl border border-line bg-panel px-5 py-4">
            <div className="text-xs uppercase tracking-wider text-ink-3">{label}</div>
            <div className="mt-1 text-lg font-semibold tabular-nums text-ink">{value}</div>
          </div>
        ))}
      </section>

      {invoice.reason_explanation ? (
        <section className="rounded-xl border border-line bg-panel px-5 py-4">
          <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-ink-3">
            Diagnosis
            <ProvenanceBadge provenance="ai" />
            {invoice.reason_llm_disagreed ? (
              <span className="rounded bg-amber-50 px-2 py-0.5 text-[10px] text-amber-700 ring-1 ring-inset ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30">
                model disagreed — rule applied
              </span>
            ) : null}
          </div>
          <p className="mt-2 text-sm leading-relaxed text-ink-2">
            {invoice.reason_explanation}
          </p>
        </section>
      ) : null}

      {invoice.dispute_history.length ? (
        <section className="rounded-xl border border-line bg-panel px-5 py-4">
          <div className="text-xs uppercase tracking-wider text-ink-3">
            Resolved disputes ({invoice.dispute_history.length})
          </div>
          <div className="mt-2 space-y-2">
            {invoice.dispute_history.map((past) => (
              <div key={past.id} className="text-sm text-ink-2">
                <span className="text-ink">{past.reason}</span>
                {past.resolution_note ? (
                  <span className="text-ink-3"> — “{past.resolution_note}”</span>
                ) : null}
                <span className="text-ink-4">
                  {" "}
                  · closed by {past.resolved_by?.replace(/^human:/, "") ?? "—"}
                  {past.recovery_resumed_at ? ", recovery resumed" : ", recovery left stopped"}
                </span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {invoice.payment_url ? (
        <section className="rounded-xl border border-line bg-panel px-5 py-4">
          <div className="text-xs uppercase tracking-wider text-ink-3">Payment link</div>
          <a
            href={invoice.payment_url}
            target="_blank"
            rel="noreferrer"
            className="mt-1 block font-mono text-sm text-accent hover:underline"
          >
            {invoice.payment_url}
          </a>
          <div className="mt-1 text-xs text-ink-3">
            Razorpay status: {invoice.payment_link_status ?? "—"}
          </div>
        </section>
      ) : (
        <ProvisionButton invoiceId={invoice.id} />
      )}

      <div className="grid gap-6 lg:grid-cols-[1.15fr_1fr]">
        <section className="space-y-6">
          <div>
            <h2 className="mb-1 text-sm font-semibold text-ink">
              Conversation
              <span className="ml-2 font-normal text-ink-3">
                everything said about this invoice, in order
              </span>
            </h2>
            <p className="mb-3 text-xs text-ink-3">
              {invoice.reply_count > 0
                ? `${invoice.reply_count} customer repl${
                    invoice.reply_count === 1 ? "y" : "ies"
                  } — this customer has replied, so they are never classified unresponsive.`
                : "No customer replies yet."}
            </p>
            <Conversation entries={invoice.conversation} />
          </div>

          <div>
          <h2 className="mb-3 text-sm font-semibold text-ink">
            Timeline
            <span className="ml-2 font-normal text-ink-3">
              provisioned → chased → reconciled
            </span>
          </h2>
          <ol className="space-y-1.5">
            {invoice.timeline.map((entry, i) => (
              <li
                key={i}
                className="flex items-start gap-3 rounded-lg border border-line-2 bg-panel px-3 py-2"
              >
                <span className="w-24 shrink-0 pt-0.5 font-mono text-[11px] text-ink-4">
                  {time(entry.at)}
                </span>
                <ProvenanceBadge provenance={entry.provenance} />
                <span className="text-sm text-ink-2">{entry.summary}</span>
              </li>
            ))}
          </ol>
          </div>
        </section>

        <section className="space-y-5">
          <div>
            <h2 className="mb-3 text-sm font-semibold text-ink">
              Reminders sent ({invoice.reminders.length} of 3)
            </h2>
            <div className="space-y-4">
              {invoice.reminders.map((reminder) => (
                <div
                  key={reminder.tier}
                  className="rounded-xl border border-line bg-panel"
                >
                  <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
                    <span className="text-sm font-medium text-ink">
                      Tier {reminder.tier}
                    </span>
                    <span className="rounded bg-panel-2 px-2 py-0.5 text-[11px] text-ink-2">
                      {reminder.tone}
                    </span>
                    <span className="rounded bg-panel-2 px-2 py-0.5 font-mono text-[10px] text-ink-3">
                      {reminder.generated_by}
                    </span>
                    {reminder.llm_degraded ? (
                      <span className="rounded bg-amber-50 px-2 py-0.5 text-[10px] text-amber-700 ring-1 ring-inset ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30">
                        degraded
                      </span>
                    ) : null}
                  </div>
                  <div className="px-4 py-3">
                    <div className="text-sm font-medium text-ink">{reminder.subject}</div>
                    <pre className="mt-2 whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-ink-3">
                      {reminder.body}
                    </pre>
                  </div>
                  <div className="px-4 pb-4">
                    <PolicyCard rendered={reminder.policy_rendered} />
                  </div>
                </div>
              ))}
              {invoice.reminders.length === 0 ? (
                <p className="rounded-xl border border-line bg-panel px-4 py-6 text-center text-sm text-ink-3">
                  No reminders sent yet.
                </p>
              ) : null}
            </div>
          </div>

          {invoice.promises.length ? (
            <div>
              <h2 className="mb-3 text-sm font-semibold text-ink">Promises</h2>
              <div className="space-y-2">
                {invoice.promises.map((promise) => (
                  <div
                    key={promise.id}
                    className="rounded-lg border border-line bg-panel px-4 py-3"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm text-ink">
                        by {promise.promised_date}
                      </span>
                      <StatusBadge status={promise.status} />
                    </div>
                    <p className="mt-1.5 text-xs italic text-ink-3">“{promise.excerpt}”</p>
                    <div className="mt-1 text-[11px] text-ink-4">
                      confidence {(promise.confidence * 100).toFixed(0)}% · resumes at tier{" "}
                      {promise.tier_at_pause}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <SimulateReply invoiceId={invoice.id} />
        </section>
      </div>
    </div>
  );
}
