"use client";

/**
 * "Why is recovery paused?" — answered before the merchant has to ask.
 *
 * The one screen this feature exists for. A merchant landing here should understand,
 * without clicking anything, that Vasooli stopped chasing on purpose, what the
 * customer actually said, how the system read it, and what to do next.
 *
 * Two deliberate omissions. There is no prompt anywhere on this card, and nothing
 * about it looks like a chat interface — the AI's contribution is a labelled
 * interpretation sitting next to the customer's own words, which is what makes it
 * checkable. A chat bubble invites the merchant to argue with the model; a quote and
 * a reading invite them to go and look at the delivery note.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ProvenanceBadge } from "@/components/badges";
import type { DisputeView } from "@/lib/api";

function when(iso: string) {
  return new Date(iso).toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function DisputeCard({ dispute }: { dispute: DisputeView }) {
  const router = useRouter();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<null | "resolve" | "resume">(null);
  const [error, setError] = useState<string | null>(null);

  async function resolve(resume: boolean) {
    if (resume && !note.trim()) {
      setError("Add a decision note before resuming recovery.");
      return;
    }
    if (
      resume &&
      !window.confirm(
        "Resume automated recovery for this invoice? Future due reminders may contact the customer.",
      )
    ) {
      return;
    }
    setBusy(resume ? "resume" : "resolve");
    setError(null);
    try {
      const res = await fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: `/api/dashboard/disputes/${dispute.id}/resolve`,
          body: { note, resume_recovery: resume },
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? "Could not resolve this dispute.");
        return;
      }
      router.refresh();
    } catch {
      setError("Request failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="overflow-hidden rounded-xl border border-rose-200 bg-rose-50/70 dark:border-rose-500/30 dark:bg-rose-500/10">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-rose-200 px-5 py-3 dark:border-rose-500/30">
        <span className="text-sm font-semibold text-rose-800 dark:text-rose-200">
          Recovery is paused — the customer disputes this invoice
        </span>
        <span className="rounded bg-rose-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-rose-800 ring-1 ring-inset ring-rose-300 dark:bg-rose-500/20 dark:text-rose-200 dark:ring-rose-500/40">
          Needs review
        </span>
        <span className="ml-auto text-xs text-rose-700/80 dark:text-rose-300/70">
          Opened {when(dispute.opened_at)}
        </span>
      </div>

      <div className="space-y-4 px-5 py-4">
        <div>
          <div className="text-xs uppercase tracking-wider text-ink-3">What is disputed</div>
          <p className="mt-1 text-[15px] font-medium leading-snug text-ink">{dispute.reason}</p>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-lg border border-line bg-panel px-4 py-3">
            <div className="text-xs uppercase tracking-wider text-ink-3">
              What the customer wrote
            </div>
            <p className="mt-1.5 text-sm italic leading-relaxed text-ink-2">
              “{dispute.source_excerpt}”
            </p>
          </div>

          <div className="rounded-lg border border-line bg-panel px-4 py-3">
            <div className="flex flex-wrap items-center gap-2 text-xs uppercase tracking-wider text-ink-3">
              How Vasooli read it
              <ProvenanceBadge provenance="ai" />
              <span className="rounded bg-panel-2 px-2 py-0.5 font-mono text-[10px] normal-case text-ink-3">
                {dispute.detected_by}
              </span>
              <span
                title="How sure the model was. It does not affect the pause — any dispute pauses recovery."
                className="rounded bg-panel-2 px-2 py-0.5 text-[10px] normal-case text-ink-2"
              >
                {dispute.confidence_display} confident
              </span>
              {dispute.ai_degraded ? (
                <span className="rounded bg-amber-50 px-2 py-0.5 text-[10px] normal-case text-amber-700 ring-1 ring-inset ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30">
                  degraded
                </span>
              ) : null}
            </div>
            <p className="mt-1.5 text-sm leading-relaxed text-ink-2">{dispute.summary}</p>
          </div>
        </div>

        {dispute.facts.length ? (
          <div>
            <div className="text-xs uppercase tracking-wider text-ink-3">
              Claims to check ({dispute.facts.length})
            </div>
            <ul className="mt-1.5 space-y-1">
              {dispute.facts.map((fact) => (
                <li
                  key={fact}
                  className="flex items-start gap-2 text-sm text-ink-2"
                >
                  <span className="mt-[3px] text-ink-4">□</span>
                  <span>{fact}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {dispute.payment_received_while_open ? (
          <p className="rounded-lg border border-sky-200 bg-sky-50 px-4 py-2.5 text-sm text-sky-800 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-200">
            A verified Razorpay payment arrived while this dispute was open. The payment
            is recorded and the invoice balance is up to date — paying does not close the
            dispute by itself.
          </p>
        ) : null}

        <div className="rounded-lg border border-line bg-panel-2 px-4 py-3">
          <div className="text-xs uppercase tracking-wider text-ink-3">What happens next</div>
          <p className="mt-1 text-sm text-ink-2">{dispute.next_action}</p>
        </div>

        <div className="border-t border-rose-200/70 pt-4 dark:border-rose-500/25">
          <label
            htmlFor="dispute-note"
            className="text-xs uppercase tracking-wider text-ink-3"
          >
            Your decision (recorded in the audit trail)
          </label>
          <textarea
            id="dispute-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            placeholder="e.g. Checked the delivery note — 12 units were signed for."
            className="mt-1.5 w-full resize-none rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none placeholder:text-ink-4 focus:border-ink-4"
          />
          <div className="mt-2.5 flex flex-wrap items-center gap-2.5">
            <button
              onClick={() => resolve(false)}
              disabled={busy !== null}
              className="rounded-md border border-line bg-panel px-3 py-1.5 text-sm font-medium text-ink transition hover:bg-panel-2 disabled:opacity-50"
            >
              {busy === "resolve" ? "Closing…" : "Resolve — keep recovery stopped"}
            </button>
            <button
              onClick={() => resolve(true)}
              disabled={busy !== null}
              className="rounded-md bg-invert px-3 py-1.5 text-sm font-medium text-invert-ink transition hover:opacity-90 disabled:opacity-50"
            >
              {busy === "resume" ? "Resuming…" : "Resolve and resume recovery"}
            </button>
            {error ? (
              <span className="text-xs text-rose-700 dark:text-rose-300">{error}</span>
            ) : null}
          </div>
          <p className="mt-2 text-xs text-ink-3">
            Resolving and resuming are separate on purpose. Agreeing with the customer
            closes the case and leaves this invoice alone; only resume if you checked the
            paperwork and the invoice stands.
          </p>
        </div>
      </div>
    </section>
  );
}
