/**
 * The policy decision, rendered verbatim. Doc §5.
 *
 * This is the most convincing component on the dashboard. It shows every check the
 * engine ran — including the ones that failed — for a message the model drafted but
 * deterministic code had to approve. A visible "✗ Banned phrase: legal action" says
 * more about the architecture than any diagram.
 *
 * The text comes from the backend already formatted, so what a reviewer reads is the
 * decision itself and not a re-rendering of it that could drift.
 */

export function PolicyCard({ rendered }: { rendered: string | null }) {
  if (!rendered) return null;

  const lines = rendered.split("\n");
  const approved = rendered.includes("Result: APPROVED");

  return (
    <div className="rounded-lg border border-line bg-panel-2">
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <span className="text-xs font-medium uppercase tracking-wider text-ink-3">
          Policy engine
        </span>
        <span
          className={`rounded px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${
            approved
              ? "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30"
              : "bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30"
          }`}
        >
          {approved ? "APPROVED" : "BLOCKED"}
        </span>
      </div>
      <pre className="overflow-x-auto px-4 py-3 font-mono text-[12.5px] leading-relaxed">
        {lines.map((line, i) => {
          const pass = line.startsWith("✓");
          const fail = line.startsWith("✗");
          return (
            <div
              key={i}
              className={
                fail
                  ? "text-rose-700 dark:text-rose-300"
                  : pass
                    ? "text-emerald-700 dark:text-emerald-300/90"
                    : line.startsWith("Result:")
                      ? "mt-1 font-semibold text-ink"
                      : "text-ink-3"
              }
            >
              {line}
            </div>
          );
        })}
      </pre>
    </div>
  );
}
