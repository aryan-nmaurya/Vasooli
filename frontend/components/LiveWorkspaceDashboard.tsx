"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { ReasonBadge, StatusBadge, TierBadge } from "@/components/badges";
import { LiveSignInPrompt } from "@/components/LiveSignInPrompt";
import { liveGet } from "@/lib/live-api";

const POLL_MS = 5000;

type Overview = {
  total_overdue_paise: number; total_overdue_display: string;
  recovered_paise: number; recovered_display: string;
  recovery_rate_display: string; avg_days_to_recovery: number | null;
  automation_rate_display: string; invoices_total: number; invoices_recovered: number;
  invoices_in_human_review: number; active_promises: number; broken_promises: number;
  counts_by_status: Record<string, number>; counts_by_reason: Record<string, number>;
};
type QueueRow = {
  id: string; invoice_number: string; customer_name: string; amount_display: string;
  outstanding_paise: number; status: string; days_overdue: number; tier_label: string;
  reason_category: string | null; why: string; next_action: string; dispute_open: boolean;
};
type Exceptions = { total: number; reconciliation: unknown[]; communication: unknown[]; unclosed_links: unknown[]; inbound: unknown[] };
type Readiness = { status: "ready" | "attention" | "degraded"; summary: string };

const TONE = { plain: "text-ink", good: "text-emerald-700 dark:text-emerald-300", bad: "text-rose-700 dark:text-rose-300" };

function Metric({ label, value, sub, tone = "plain", compact = false, flash = false }: { label: string; value: string; sub?: string; tone?: keyof typeof TONE; compact?: boolean; flash?: boolean }) {
  return <div className={`relative overflow-hidden rounded-xl border border-line bg-panel ${compact ? "px-4 py-3.5" : "px-5 py-5"} ${flash ? "border-emerald-400 bg-emerald-50 dark:border-emerald-500/60 dark:bg-emerald-500/10" : ""}`}>
    {tone !== "plain" ? <span aria-hidden className={`absolute inset-y-0 left-0 w-0.5 ${tone === "good" ? "bg-emerald-500" : "bg-rose-500"}`} /> : null}
    <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">{label}</p>
    <p className={`${compact ? "mt-1 text-xl" : "mt-2 text-3xl"} font-semibold tabular-nums tracking-[-0.03em] ${TONE[tone]}`}>{value}</p>
    {sub ? <p className="mt-1 text-xs leading-4 text-ink-3">{sub}</p> : null}
  </div>;
}

function friendlyError(errors: unknown[]) {
  const message = errors.map((error) => error instanceof Error ? error.message : String(error)).join(" ");
  return message.includes("Not Found")
    ? "The running API does not have the live dashboard routes yet. Restart or redeploy the backend, then refresh this page."
    : "The live workspace is temporarily unavailable. The figures below are the last successful read.";
}

export function LiveWorkspaceDashboard() {
  const [merchant, setMerchant] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [queue, setQueue] = useState<QueueRow[]>([]);
  const [exceptions, setExceptions] = useState<Exceptions | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [reasonFilter, setReasonFilter] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [staleSince, setStaleSince] = useState<Date | null>(null);
  const [flash, setFlash] = useState(false);
  const lastRecovered = useRef(0);

  useEffect(() => {
    const id = window.localStorage.getItem("vasooli_live_merchant") || "";
    setMerchant(id);
    if (!id) return;
    let alive = true;
    async function load() {
      const results = await Promise.allSettled([
        liveGet<Overview>("/api/live/workspace/overview", id),
        liveGet<QueueRow[]>("/api/live/workspace/queue?limit=200", id),
        liveGet<Exceptions>("/api/live/workspace/exceptions", id),
        liveGet<Readiness>("/api/live/operations/readiness", id),
      ]);
      if (!alive) return;
      const failures = results.filter((result) => result.status === "rejected").map((result) => result.reason);
      const [metrics, rows, failed, health] = results;
      if (metrics.status === "fulfilled") {
        if (lastRecovered.current && metrics.value.recovered_paise > lastRecovered.current) {
          setFlash(true); window.setTimeout(() => setFlash(false), 2200);
        }
        lastRecovered.current = metrics.value.recovered_paise; setOverview(metrics.value);
      }
      if (rows.status === "fulfilled") setQueue(rows.value);
      if (failed.status === "fulfilled") setExceptions(failed.value);
      if (health.status === "fulfilled") setReadiness(health.value);
      if (failures.length) { setError(friendlyError(failures)); setStaleSince((current) => current ?? new Date()); }
      else { setError(null); setStaleSince(null); }
    }
    void load();
    const interval = window.setInterval(load, POLL_MS);
    return () => { alive = false; window.clearInterval(interval); };
  }, []);

  const visible = useMemo(() => queue.filter((row) => (!statusFilter || row.status === statusFilter) && (!reasonFilter || row.reason_category === reasonFilter)), [queue, reasonFilter, statusFilter]);
  if (!merchant) return <div className="mx-auto max-w-6xl py-8"><LiveSignInPrompt what="Your recovery workspace" /></div>;

  const statuses = Object.entries(overview?.counts_by_status ?? {}).sort((a, b) => b[1] - a[1]);
  const reasons = Object.entries(overview?.counts_by_reason ?? {}).sort((a, b) => b[1] - a[1]);

  return <div className="space-y-6">
    {readiness ? <section className={`rounded-xl border px-5 py-4 ${readiness.status === "ready" ? "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-200" : readiness.status === "degraded" ? "border-rose-300 bg-rose-50 text-rose-900 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200" : "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200"}`}>
      <div className="flex items-center gap-2"><span className={`size-2 rounded-full ${readiness.status === "ready" ? "bg-emerald-500" : readiness.status === "degraded" ? "bg-rose-500" : "bg-amber-500"}`} /><h2 className="text-sm font-semibold">{readiness.status === "ready" ? "Automation is running on schedule." : readiness.status === "degraded" ? "Automation needs attention." : "Some background work is delayed."}</h2><Link href="/live/readiness" className="ml-auto text-xs underline underline-offset-2">System health</Link></div>
      <p className="mt-1 text-xs opacity-80">{readiness.summary}</p>
    </section> : null}

    <div className="flex flex-wrap items-start justify-between gap-x-8 gap-y-5">
      <div><div className="mb-2 flex items-center gap-2"><span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">Dashboard</span><span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-300"><span className="size-1.5 rounded-full bg-emerald-500" /> Live</span></div><h1 className="text-2xl font-semibold tracking-[-0.03em] text-ink sm:text-[1.75rem]">Recovery overview</h1><p className="mt-1.5 max-w-2xl text-sm leading-6 text-ink-3">Monitor overdue value, act on the recovery queue, and keep exceptions moving.</p></div>
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-panel p-1.5 shadow-sm"><Link href="/live/invoices#import" className="inline-flex min-h-9 items-center rounded-lg bg-invert px-3.5 py-2 text-xs font-semibold text-invert-ink">Import invoices</Link><Link href="/live/integrations" className="inline-flex min-h-9 items-center rounded-lg px-3 py-2 text-xs font-medium text-ink-2 ring-1 ring-inset ring-line hover:bg-panel-2">Sync ERP</Link></div>
    </div>

    {error ? <div role="status" className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-2.5 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200"><p>{error}</p>{staleSince ? <p className="mt-1 text-xs opacity-75">Last successful figures remain visible while the connection recovers.</p> : null}</div> : null}

    {!overview ? <p className="text-sm text-ink-4">Loading recovery overview…</p> : <>
      <section aria-label="Recovery performance" className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4"><Metric label="Total overdue" value={overview.total_overdue_display} sub={`${overview.invoices_total} invoices`} /><Metric label="Recovered" value={overview.recovered_display} sub={`${overview.invoices_recovered} settled`} tone="good" flash={flash} /><Metric label="Recovery rate" value={overview.recovery_rate_display} sub="by value, not count" /><Metric label="Avg days to recovery" value={overview.avg_days_to_recovery?.toFixed(1) ?? "—"} sub={`${overview.automation_rate_display} without a human`} /></section>
      <section className="rounded-xl border border-line bg-panel p-4 sm:p-5"><div className="mb-3 flex items-center justify-between"><div><h2 className="text-sm font-semibold">Operational signals</h2><p className="mt-0.5 text-xs text-ink-3">Commitments and exceptions that may need intervention.</p></div><span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-4">Now</span></div><div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4"><Metric compact label="Active promises" value={String(overview.active_promises)} /><Metric compact label="Broken promises" value={String(overview.broken_promises)} sub={overview.broken_promises ? "flagged" : undefined} tone={overview.broken_promises ? "bad" : "plain"} /><Metric compact label="Needs attention" value={String(exceptions?.total ?? 0)} sub="failed payments or reminders" tone={exceptions?.total ? "bad" : "plain"} /><Metric compact label="Needs a human" value={String(overview.invoices_in_human_review)} sub="outside the automated cadence" tone={overview.invoices_in_human_review ? "bad" : "plain"} /></div></section>
      {overview.invoices_total === 0 ? <section className="rounded-xl border border-dashed border-line bg-panel p-5"><p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-accent">Your workspace is ready</p><h2 className="mt-2 text-lg font-semibold">Bring in your first receivables.</h2><p className="mt-1 max-w-2xl text-sm text-ink-3">Import a CSV now or connect your ERP. Sender identity, policy, billing, and team access remain available from the dashboard—there is no separate onboarding gate.</p><div className="mt-4 flex flex-wrap gap-2"><Link href="/live/invoices#import" className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white">Import CSV</Link><Link href="/live/integrations" className="rounded-lg border border-line px-4 py-2 text-sm">Connect ERP</Link><Link href="/live/settings" className="rounded-lg border border-line px-4 py-2 text-sm">Configure sending</Link></div></section> : null}
    </>}

    <section className="rounded-xl border border-line bg-panel p-3 sm:p-5">
      <div className="mb-4 flex flex-wrap items-start gap-3"><div><h2 className="text-base font-semibold">Recovery queue</h2><p className="mt-0.5 text-xs text-ink-3">Prioritized invoices ready for the next collection step.</p></div><span className="rounded-full bg-panel-2 px-2.5 py-1 text-[11px] font-medium text-ink-3">{visible.length} of {queue.length} shown</span>{statusFilter || reasonFilter ? <button onClick={() => { setStatusFilter(null); setReasonFilter(null); }} className="text-xs text-accent hover:underline">Clear filters</button> : null}</div>
      {statuses.length || reasons.length ? <div className="rounded-lg border border-line-2 bg-surface p-3">{statuses.length ? <div className="flex flex-wrap items-center gap-1.5"><span className="mr-1 text-[11px] uppercase tracking-wider text-ink-4">Status</span>{statuses.map(([status, count]) => <button key={status} onClick={() => setStatusFilter(statusFilter === status ? null : status)} className={`rounded-md px-2.5 py-1 text-xs ring-1 ring-inset ring-line ${statusFilter === status ? "bg-panel-2 font-medium text-ink" : "text-ink-3 hover:bg-panel-2"}`}>{status.replaceAll("_", " ")} ({count})</button>)}</div> : null}{reasons.length ? <div className="mt-2.5 flex flex-wrap items-center gap-1.5"><span className="mr-1 text-[11px] uppercase tracking-wider text-ink-4">Reason</span><button onClick={() => setReasonFilter(null)} className={`rounded-md px-2.5 py-1 text-xs ring-1 ring-inset ring-line ${reasonFilter === null ? "bg-panel-2 font-medium text-ink" : "text-ink-3"}`}>All</button>{reasons.map(([reason, count]) => <button key={reason} onClick={() => setReasonFilter(reasonFilter === reason ? null : reason)} className={`rounded-md px-2.5 py-1 text-xs ring-1 ring-inset ring-line ${reasonFilter === reason ? "bg-panel-2 font-medium text-ink" : "text-ink-3 hover:bg-panel-2"}`}>{reason.replaceAll("_", " ")} ({count})</button>)}</div> : null}</div> : null}
      <div className="scroll-x mt-3 rounded-lg border border-line"><table className="w-full min-w-[860px] text-sm"><thead className="border-b border-line text-left text-xs uppercase tracking-wider text-ink-3"><tr><th className="px-4 py-2.5 font-medium">Invoice</th><th className="px-4 py-2.5 font-medium">Customer</th><th className="px-4 py-2.5 text-right font-medium">Amount</th><th className="px-4 py-2.5 text-right font-medium">Overdue</th><th className="px-4 py-2.5 font-medium">Tier</th><th className="px-4 py-2.5 font-medium">Reason</th><th className="px-4 py-2.5 font-medium">Status</th><th className="px-4 py-2.5 font-medium">Why</th></tr></thead><tbody className="divide-y divide-line-2">{visible.map((row) => <tr key={row.id} className="transition hover:bg-panel-2"><td className="px-4 py-2.5"><Link href={`/live/invoices/${row.id}`} className="font-mono text-[13px] text-accent hover:underline">{row.invoice_number}</Link></td><td className="px-4 py-2.5 text-ink-2">{row.customer_name}</td><td className="px-4 py-2.5 text-right font-medium tabular-nums">{row.amount_display}</td><td className="px-4 py-2.5 text-right tabular-nums text-ink-3">{row.days_overdue}d</td><td className="px-4 py-2.5"><TierBadge label={row.tier_label} /></td><td className="px-4 py-2.5"><ReasonBadge reason={row.reason_category} /></td><td className="px-4 py-2.5"><div className="flex flex-wrap gap-1.5"><StatusBadge status={row.status} />{row.dispute_open ? <span className="rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-rose-700 ring-1 ring-inset ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30">Disputed</span> : null}</div></td><td className="max-w-[300px] px-4 py-2.5 text-xs text-ink-3">{row.why}</td></tr>)}{visible.length === 0 ? <tr><td colSpan={8} className="px-4 py-10 text-center text-sm text-ink-3">No invoices in this view. Import a CSV or sync your ERP to begin.</td></tr> : null}</tbody></table></div>
    </section>

    <section className="rounded-xl border border-line bg-panel p-4 sm:p-5"><div className="flex items-start justify-between gap-4"><div><h2 className="text-base font-semibold">Operational exceptions</h2><p className="mt-0.5 text-xs text-ink-3">Failures remain visible until they are resolved or retried.</p></div><Link href="/live/exceptions" className="text-xs font-semibold text-accent">Open exception queue →</Link></div><div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[["Payment reconciliation", exceptions?.reconciliation.length ?? 0], ["Communication", exceptions?.communication.length ?? 0], ["Payment-link closure", exceptions?.unclosed_links.length ?? 0], ["Inbound replies", exceptions?.inbound.length ?? 0]].map(([label, count]) => <div key={String(label)} className="rounded-lg border border-line-2 bg-surface px-4 py-3"><p className="text-xs text-ink-3">{label}</p><p className={`mt-1 text-xl font-semibold ${Number(count) ? "text-rose-700 dark:text-rose-300" : "text-ink"}`}>{count}</p></div>)}</div></section>
  </div>;
}
