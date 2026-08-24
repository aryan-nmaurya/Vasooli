/**
 * "Why is Vasooli doing this?" — the first thing on an invoice page.
 *
 * A merchant should not have to read a timeline to answer that. The sentence comes
 * from the backend and is generated deterministically from the same state the policy
 * engine reads, so it cannot drift from the decision it describes. A generated
 * explanation that disagrees with the action is worse than none — it teaches people to
 * distrust the whole screen.
 */

const STATE_STYLES: Record<string, string> = {
  active:
    "border-sky-200 bg-sky-50 dark:border-sky-500/30 dark:bg-sky-500/10",
  paused:
    "border-violet-200 bg-violet-50 dark:border-violet-500/30 dark:bg-violet-500/10",
  stopped:
    "border-emerald-200 bg-emerald-50 dark:border-emerald-500/30 dark:bg-emerald-500/10",
  waiting: "border-line bg-panel",
};

const STATE_LABELS: Record<string, string> = {
  active: "Chasing",
  paused: "Paused",
  stopped: "Stopped",
  waiting: "Waiting",
};

export function WhyCard({
  why,
  next,
  state,
}: {
  why: string;
  next: string;
  state: string;
}) {
  return (
    <section className={`rounded-xl border px-5 py-4 ${STATE_STYLES[state] ?? STATE_STYLES.waiting}`}>
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium uppercase tracking-wider text-ink-3">
          Why is Vasooli doing this?
        </span>
        <span className="rounded bg-panel-2 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-ink-2 ring-1 ring-inset ring-line">
          {STATE_LABELS[state] ?? state}
        </span>
      </div>
      <p className="mt-2 text-[15px] leading-snug text-ink">{why}</p>
      <p className="mt-1 text-sm text-ink-3">{next}</p>
    </section>
  );
}
