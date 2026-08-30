/**
 * Whether the agent is actually running.
 *
 * The runtime banner reports configuration — "scheduler: enabled" — which the audit
 * correctly refused to accept as evidence. APScheduler runs inside the API process; if
 * its thread dies, the API stays healthy, /health stays green, invoices keep ageing,
 * and nobody is chased. Configuration would still say "enabled" the whole time.
 *
 * This reads the job-run history instead: when each job last succeeded, what it did,
 * and when it is due next. A server component, so the verdict is computed by the
 * backend and rendered without a round trip from the browser.
 */

import type { AutomationHealth as Health, AutomationJob } from "@/lib/api";

const TONE: Record<AutomationJob["state"], string> = {
  healthy:
    "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-200",
  stale:
    "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200",
  failing:
    "border-rose-300 bg-rose-50 text-rose-900 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200",
  unknown: "border-line bg-panel-2 text-ink-2",
  disabled: "border-line bg-panel-2 text-ink-2",
};

const DOT: Record<AutomationJob["state"], string> = {
  healthy: "bg-emerald-500",
  stale: "bg-amber-500",
  failing: "bg-rose-500",
  unknown: "bg-ink-4",
  disabled: "bg-ink-4",
};

const HEADLINE: Record<AutomationJob["state"], string> = {
  healthy: "Automation is running on schedule.",
  stale: "Automation has not run recently.",
  failing: "Automation is failing.",
  unknown: "No automation history on this deployment yet.",
  disabled: "The scheduler is switched off here.",
};

function when(iso: string | null): string {
  if (!iso) return "never";
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 0) return new Date(iso).toLocaleString("en-IN", { timeStyle: "short" });
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function due(iso: string | null): string {
  if (!iso) return "not scheduled here";
  const seconds = Math.floor((new Date(iso).getTime() - Date.now()) / 1000);
  if (seconds <= 0) return "due now";
  if (seconds < 3600) return `in ${Math.max(1, Math.floor(seconds / 60))}m`;
  if (seconds < 86400) return `in ${Math.floor(seconds / 3600)}h`;
  return `in ${Math.floor(seconds / 86400)}d`;
}

function summarise(detail: Record<string, unknown>): string | null {
  const parts = Object.entries(detail)
    .filter(([, value]) => typeof value === "number" && value > 0)
    .map(([key, value]) => `${value} ${key.replace(/_/g, " ")}`);
  return parts.length ? parts.slice(0, 4).join(" · ") : null;
}

export function AutomationHealth({ health }: { health: Health }) {
  return (
    <section className={`rounded-xl border px-5 py-4 ${TONE[health.overall]}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className={`h-2 w-2 rounded-full ${DOT[health.overall]}`} aria-hidden />
        <h2 className="text-sm font-semibold">{HEADLINE[health.overall]}</h2>
        <span className="ml-auto text-[11px] opacity-70">
          checked {when(health.checked_at)}
        </span>
      </div>

      <p className="mt-1 max-w-prose text-xs opacity-80">
        Read from recorded job runs, not from configuration. A scheduler that has stopped
        leaves the API healthy and nothing chased — so “enabled” is not evidence that a
        cycle happened.
      </p>

      <ul className="mt-3 grid gap-2 sm:grid-cols-2">
        {health.jobs.map((job) => {
          const did = summarise(job.last_detail);
          return (
            <li
              key={job.job_id}
              className="rounded-lg border border-current/15 bg-panel/60 px-3 py-2 text-xs"
            >
              <div className="flex items-center gap-2">
                <span className={`h-1.5 w-1.5 rounded-full ${DOT[job.state]}`} aria-hidden />
                <span className="font-medium text-ink">{job.label}</span>
                <span className="ml-auto tabular-nums text-ink-3">
                  {when(job.last_success_at)}
                </span>
              </div>
              <p className="mt-1 text-ink-3">{job.explanation}</p>
              <p className="mt-0.5 text-ink-4">
                next {due(job.next_run_at)}
                {did ? ` · last run: ${did}` : ""}
              </p>
            </li>
          );
        })}
      </ul>

      {health.scheduler_enabled && !health.scheduler_running_here ? (
        <p className="mt-3 border-t border-current/15 pt-2 text-[11px] opacity-75">
          No scheduler thread in the process serving this request. Behind more than one
          worker that is expected — only one of them runs the schedule — so the job
          history above, which every process can read, is the source of truth.
        </p>
      ) : null}
    </section>
  );
}
