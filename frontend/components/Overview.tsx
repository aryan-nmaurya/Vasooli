"use client";

/**
 * Overview and recovery queue. Doc §7.
 *
 * Polls every three seconds. The demo's highest-impact moment is a real payment
 * landing and the recovered counter moving without anyone touching the page, and
 * polling gets that with no socket to keep alive — one fewer thing that can fail on
 * stage, and it degrades to "slightly late" rather than "stopped working".
 */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ReasonBadge, StatusBadge, TierBadge } from "@/components/badges";
import { RunCycleButton } from "@/components/RunCycleButton";
import { ExceptionsPanel } from "@/components/Exceptions";
import {
  getExceptions,
  getOverview,
  getQueue,
  type Exceptions,
  type Overview,
  type QueueRow,
} from "@/lib/api";

const POLL_MS = 3000;

/** Value colours, matching the promise tracker's stat cards. */
const TONE: Record<string, string> = {
  good: "text-emerald-700 dark:text-emerald-300",
  bad: "text-rose-700 dark:text-rose-300",
  plain: "text-ink",
};

function Metric({
  label,
  value,
  sub,
  flash,
  tone = "plain",
}: {
  label: string;
  value: string;
  sub?: string;
  flash?: boolean;
  tone?: keyof typeof TONE;
}) {
  return (
    <div
      // The transition exists for the payment flash. It is applied only while
      // flashing: left on permanently it also animates every theme switch, and the
      // toggle spends most of a second looking half-broken.
      className={`rounded-xl border border-line bg-panel px-5 py-4 ${
        flash
          ? "border-emerald-400 bg-emerald-50 transition-colors duration-700 dark:border-emerald-500/60 dark:bg-emerald-500/10"
          : ""
      }`}
    >
      <div className="text-xs uppercase tracking-wider text-ink-3">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums tracking-tight ${TONE[tone]}`}>
        {value}
      </div>
      {sub ? <div className="mt-0.5 text-xs text-ink-3">{sub}</div> : null}
    </div>
  );
}

export function OverviewClient({
  initialOverview,
  initialQueue,
  initialExceptions,
}: {
  initialOverview: Overview;
  initialQueue: QueueRow[];
  initialExceptions: Exceptions;
}) {
  const [overview, setOverview] = useState(initialOverview);
  const [queue, setQueue] = useState(initialQueue);
  const [exceptions, setExceptions] = useState(initialExceptions);
  const [filter, setFilter] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [flash, setFlash] = useState(false);
  //: Null until a poll fails. Surfaced rather than swallowed: a dashboard that has
  //: silently stopped updating looks identical to one where nothing is happening,
  //: which is the worst possible moment to find out on stage.
  const [staleSince, setStaleSince] = useState<Date | null>(null);
  const lastRecovered = useRef(initialOverview.recovered_paise);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const [o, q, x] = await Promise.all([
          getOverview(),
          getQueue(filter ? `&reason=${filter}` : ""),
          getExceptions(),
        ]);
        if (!alive) return;
        setExceptions(x);
        // Highlight the tile when money actually arrives — the 1:40 beat in the demo.
        if (o.recovered_paise > lastRecovered.current) {
          setFlash(true);
          setTimeout(() => setFlash(false), 2200);
        }
        lastRecovered.current = o.recovered_paise;
        setOverview(o);
        setQueue(q);
        setStaleSince(null);
      } catch {
        // One failed poll is noise; the next is three seconds away. Repeated failure
        // means the backend is gone, and the operator needs to know the numbers on
        // screen are frozen.
        setStaleSince((current) => current ?? new Date());
      }
    };
    const id = setInterval(tick, POLL_MS);
    void tick();
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [filter]);

  const reasons = Object.entries(overview.counts_by_reason).sort((a, b) => b[1] - a[1]);
  const statuses = Object.entries(overview.counts_by_status).sort((a, b) => b[1] - a[1]);
  const visible = statusFilter ? queue.filter((r) => r.status === statusFilter) : queue;

  return (
    <div className="space-y-7">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-ink">Recovery overview</h1>
          <p className="mt-1 text-sm text-ink-3">
            Recovery rate is measured by value, not by invoice count — forty small wins and one
            large miss is not a success.
          </p>
        </div>
        <RunCycleButton />
      </div>

      {staleSince ? (
        <div
          role="status"
          className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-2.5 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200"
        >
          These figures stopped updating at{" "}
          {staleSince.toLocaleTimeString("en-IN", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          })}
          . The backend is not responding — what you see below is the last good read.
        </div>
      ) : null}

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric
          label="Total overdue"
          value={overview.total_overdue_display}
          sub={`${overview.invoices_total} invoices`}
        />
        <Metric
          label="Recovered"
          value={overview.recovered_display}
          sub={`${overview.invoices_recovered} settled`}
          flash={flash}
          tone="good"
        />
        <Metric
          label="Recovery rate"
          value={overview.recovery_rate_display}
          sub="by value, not count"
        />
        <Metric
          label="Avg days to recovery"
          value={overview.avg_days_to_recovery?.toFixed(1) ?? "—"}
          sub={`${overview.automation_rate_display} without a human`}
        />
      </section>

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric label="Active promises" value={String(overview.active_promises)} />
        <Metric
          label="Broken promises"
          value={String(overview.broken_promises)}
          sub={overview.broken_promises ? "flagged" : undefined}
          tone={overview.broken_promises ? "bad" : "plain"}
        />
        <Metric
          label="Needs attention"
          value={String(exceptions.total)}
          sub="failed payments or reminders"
          tone={exceptions.total ? "bad" : "plain"}
        />
        <Metric
          label="Needs a human"
          value={String(overview.invoices_in_human_review)}
          sub="outside the automated cadence"
          tone={overview.invoices_in_human_review ? "bad" : "plain"}
        />

      </section>

      <section>
        <div className="mb-2.5 flex flex-wrap items-center gap-3">
          <h2 className="text-sm font-semibold text-ink">Recovery queue</h2>
          <span className="text-xs text-ink-3">
            {visible.length} of {queue.length}
          </span>
          {(filter || statusFilter) && (
            <button
              onClick={() => {
                setFilter(null);
                setStatusFilter(null);
              }}
              className="text-xs text-accent hover:underline"
            >
              Clear filters
            </button>
          )}
        </div>

        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-[11px] uppercase tracking-wider text-ink-4">
            Status
          </span>
          {statuses.map(([status, count]) => (
            <button
              key={status}
              onClick={() => setStatusFilter(status === statusFilter ? null : status)}
              className={`rounded-md px-2.5 py-1 text-xs ring-1 ring-inset transition ${
                statusFilter === status
                  ? "bg-panel-2 font-medium text-ink ring-line"
                  : "text-ink-3 ring-line hover:bg-panel-2 hover:text-ink"
              }`}
            >
              {status.replace(/_/g, " ")} ({count})
            </button>
          ))}
        </div>

        <div className="mb-3 flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-[11px] uppercase tracking-wider text-ink-4">
            Reason
          </span>
          <button
            onClick={() => setFilter(null)}
            className={`rounded-md px-2.5 py-1 text-xs ring-1 ring-inset transition ${
              filter === null
                ? "bg-panel-2 font-medium text-ink ring-line"
                : "text-ink-3 ring-line hover:bg-panel-2 hover:text-ink"
            }`}
          >
            All
          </button>
          {reasons.map(([reason, count]) => (
            <button
              key={reason}
              onClick={() => setFilter(reason === filter ? null : reason)}
              className={`rounded-md px-2.5 py-1 text-xs ring-1 ring-inset transition ${
                filter === reason
                  ? "bg-panel-2 font-medium text-ink ring-line"
                  : "text-ink-3 ring-line hover:bg-panel-2 hover:text-ink"
              }`}
            >
              {reason.replace(/_/g, " ")} ({count})
            </button>
          ))}
        </div>

        <div className="scroll-x rounded-xl border border-line">
          <table className="w-full min-w-[860px] text-sm">
            <thead className="border-b border-line text-left text-xs uppercase tracking-wider text-ink-3">
              <tr>
                <th className="px-4 py-2.5 font-medium">Invoice</th>
                <th className="px-4 py-2.5 font-medium">Customer</th>
                <th className="px-4 py-2.5 text-right font-medium">Amount</th>
                <th className="px-4 py-2.5 text-right font-medium">Overdue</th>
                <th className="px-4 py-2.5 font-medium">Tier</th>
                <th className="px-4 py-2.5 font-medium">Reason</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">Why</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line-2">
              {visible.map((row) => (
                <tr key={row.id} className="transition hover:bg-panel-2">
                  <td className="px-4 py-2.5">
                    <Link
                      href={`/invoices/${row.id}`}
                      className="font-mono text-[13px] text-accent hover:underline"
                    >
                      {row.invoice_number}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5 text-ink-2">{row.customer_name}</td>
                  <td className="px-4 py-2.5 text-right font-medium tabular-nums text-ink">
                    {row.amount_display}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-ink-3">
                    {row.days_overdue}d
                  </td>
                  <td className="px-4 py-2.5">
                    <TierBadge label={row.tier_label} />
                  </td>
                  <td className="px-4 py-2.5">
                    <ReasonBadge reason={row.reason_category} />
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <StatusBadge status={row.status} />
                      {/* A disputed invoice is already human_review, but the badge
                          alone does not say WHY it left automation. This does. */}
                      {row.dispute_open ? (
                        <span
                          title="Recovery is paused — the customer disputes this invoice"
                          className="rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-rose-700 ring-1 ring-inset ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30"
                        >
                          Disputed
                        </span>
                      ) : null}
                    </div>
                  </td>
                  <td className="max-w-[300px] px-4 py-2.5 text-xs text-ink-3">
                    {row.why}
                  </td>
                </tr>
              ))}
              {visible.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-10 text-center text-sm text-ink-3">
                    Nothing in the queue.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      <ExceptionsPanel data={exceptions} />
    </div>
  );
}
