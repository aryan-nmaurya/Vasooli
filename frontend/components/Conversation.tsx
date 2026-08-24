/**
 * The whole conversation about one invoice, in order.
 *
 * Every event already lives on the audit timeline next to this, but a timeline
 * answers "what did the system do?" and this answers "what was said?". They are
 * different questions, and the second one is the one a merchant asks when a customer
 * is unhappy.
 *
 * The colour of the left rail is the only encoding of who spoke — customer messages
 * read as quotes, everything else as annotation around them. That keeps the customer's
 * words the loudest thing on the page, which is the point.
 */

import type { ConversationEntry, ConversationKind } from "@/lib/api";

const KIND_STYLES: Record<ConversationKind, { rail: string; label: string }> = {
  customer_message: {
    rail: "border-l-sky-400 dark:border-l-sky-500",
    label: "bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-500/15 dark:text-sky-300 dark:ring-sky-500/30",
  },
  system_message: {
    rail: "border-l-zinc-300 dark:border-l-zinc-600",
    label:
      "bg-zinc-100 text-zinc-600 ring-zinc-200 dark:bg-zinc-500/15 dark:text-zinc-300 dark:ring-zinc-500/30",
  },
  ai_analysis: {
    rail: "border-l-violet-400 dark:border-l-violet-500",
    label:
      "bg-violet-50 text-violet-700 ring-violet-200 dark:bg-violet-500/15 dark:text-violet-300 dark:ring-violet-500/30",
  },
  policy_decision: {
    rail: "border-l-emerald-400 dark:border-l-emerald-500",
    label:
      "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30",
  },
  human_action: {
    rail: "border-l-amber-400 dark:border-l-amber-500",
    label:
      "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30",
  },
  payment_event: {
    rail: "border-l-teal-400 dark:border-l-teal-500",
    label:
      "bg-teal-50 text-teal-700 ring-teal-200 dark:bg-teal-500/15 dark:text-teal-300 dark:ring-teal-500/30",
  },
};

const KIND_LABELS: Record<ConversationKind, string> = {
  customer_message: "Customer",
  system_message: "Sent",
  ai_analysis: "AI",
  policy_decision: "Policy",
  human_action: "You",
  payment_event: "Payment",
};

function when(iso: string) {
  return new Date(iso).toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** The handful of meta keys worth showing, in a fixed order. */
function metaChips(meta: Record<string, unknown>): string[] {
  const chips: string[] = [];
  if (typeof meta.confidence === "number") {
    chips.push(`${Math.round(meta.confidence * 100)}% confident`);
  }
  if (typeof meta.model === "string") chips.push(meta.model);
  if (typeof meta.tone === "string") chips.push(meta.tone);
  if (typeof meta.amount_display === "string") chips.push(meta.amount_display);
  if (Array.isArray(meta.facts) && meta.facts.length) {
    chips.push(`${meta.facts.length} claim${meta.facts.length === 1 ? "" : "s"}`);
  }
  return chips;
}

export function Conversation({ entries }: { entries: ConversationEntry[] }) {
  if (!entries.length) {
    return (
      <p className="rounded-xl border border-line bg-panel px-4 py-6 text-center text-sm text-ink-3">
        Nothing has been said on this invoice yet.
      </p>
    );
  }

  return (
    <ol className="space-y-2">
      {entries.map((entry, i) => {
        const style = KIND_STYLES[entry.kind] ?? KIND_STYLES.system_message;
        const chips = metaChips(entry.meta);
        const isCustomer = entry.kind === "customer_message";

        return (
          <li
            key={`${entry.at}-${i}`}
            className={`rounded-lg border border-line border-l-[3px] bg-panel px-4 py-3 ${style.rail}`}
          >
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider ring-1 ring-inset ${style.label}`}
              >
                {KIND_LABELS[entry.kind] ?? entry.kind}
              </span>
              <span className="text-sm font-medium text-ink">{entry.speaker}</span>
              <span className="ml-auto font-mono text-[11px] text-ink-4">
                {when(entry.at)}
              </span>
            </div>

            <p className="mt-1 text-sm text-ink-2">{entry.headline}</p>

            {entry.body ? (
              <pre
                className={`mt-2 whitespace-pre-wrap font-sans text-[13px] leading-relaxed ${
                  isCustomer ? "italic text-ink" : "text-ink-3"
                }`}
              >
                {isCustomer ? `“${entry.body}”` : entry.body}
              </pre>
            ) : null}

            {chips.length ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {chips.map((chip) => (
                  <span
                    key={chip}
                    className="rounded bg-panel-2 px-2 py-0.5 text-[10px] text-ink-3"
                  >
                    {chip}
                  </span>
                ))}
              </div>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
