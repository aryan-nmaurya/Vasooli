/**
 * Status, reason, tone, and provenance badges.
 *
 * The provenance badge is the one that earns its place: labelling each timeline step
 * as AI / policy / Razorpay / template makes the architecture's central claim — the
 * model reasons, deterministic code decides — visible without a slide.
 *
 * Light and dark need genuinely different treatments here: a soft tint reads well on
 * white but disappears on near-black, where a translucent glow works instead. So
 * these carry explicit dark: variants rather than semantic tokens.
 */

const REASON_STYLES: Record<string, string> = {
  oversight:
    "bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-500/15 dark:text-sky-300 dark:ring-sky-500/30",
  cash_constrained:
    "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30",
  dispute_likely:
    "bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30",
  unresponsive:
    "bg-zinc-100 text-zinc-600 ring-zinc-200 dark:bg-zinc-500/15 dark:text-zinc-300 dark:ring-zinc-500/30",
};

const REASON_LABELS: Record<string, string> = {
  oversight: "Oversight",
  cash_constrained: "Cash-constrained",
  dispute_likely: "Dispute-likely",
  unresponsive: "Unresponsive",
};

const STATUS_STYLES: Record<string, string> = {
  recovered:
    "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30",
  chasing:
    "bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-500/15 dark:text-sky-300 dark:ring-sky-500/30",
  promise_active:
    "bg-violet-50 text-violet-700 ring-violet-200 dark:bg-violet-500/15 dark:text-violet-300 dark:ring-violet-500/30",
  human_review:
    "bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30",
  partially_paid:
    "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30",
  pending:
    "bg-zinc-100 text-zinc-600 ring-zinc-200 dark:bg-zinc-500/15 dark:text-zinc-300 dark:ring-zinc-500/30",
  written_off:
    "bg-zinc-100 text-zinc-500 ring-zinc-200 dark:bg-zinc-500/15 dark:text-zinc-400 dark:ring-zinc-500/30",
  active:
    "bg-violet-50 text-violet-700 ring-violet-200 dark:bg-violet-500/15 dark:text-violet-300 dark:ring-violet-500/30",
  kept: "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30",
  broken:
    "bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30",
};

const PROVENANCE_STYLES: Record<string, string> = {
  ai: "bg-violet-50 text-violet-700 ring-violet-200 dark:bg-violet-500/15 dark:text-violet-300 dark:ring-violet-500/30",
  policy:
    "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30",
  razorpay:
    "bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-500/15 dark:text-sky-300 dark:ring-sky-500/30",
  system:
    "bg-zinc-100 text-zinc-600 ring-zinc-200 dark:bg-zinc-500/15 dark:text-zinc-400 dark:ring-zinc-500/30",
  human:
    "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30",
};

const base =
  "inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset whitespace-nowrap";

export function ReasonBadge({ reason }: { reason: string | null }) {
  if (!reason) return <span className="text-xs text-ink-4">—</span>;
  return (
    <span className={`${base} ${REASON_STYLES[reason] ?? REASON_STYLES.unresponsive}`}>
      {REASON_LABELS[reason] ?? reason}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`${base} ${STATUS_STYLES[status] ?? STATUS_STYLES.pending}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

export function ProvenanceBadge({ provenance }: { provenance: string }) {
  const labels: Record<string, string> = {
    ai: "AI",
    policy: "POLICY",
    razorpay: "RAZORPAY",
    system: "SYSTEM",
    human: "HUMAN",
  };
  return (
    <span
      className={`${base} w-[76px] justify-center font-mono text-[10px] tracking-wider ${
        PROVENANCE_STYLES[provenance] ?? PROVENANCE_STYLES.system
      }`}
    >
      {labels[provenance] ?? provenance.toUpperCase()}
    </span>
  );
}

export function TierBadge({ label }: { label: string }) {
  const isHuman = label === "Human";
  return (
    <span
      className={`${base} ${
        isHuman
          ? "bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30"
          : "bg-panel-2 text-ink-2 ring-line"
      }`}
    >
      {label}
    </span>
  );
}
