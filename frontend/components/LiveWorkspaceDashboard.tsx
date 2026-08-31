"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { LiveSignInPrompt } from "@/components/LiveSignInPrompt";
import { liveGet } from "@/lib/live-api";

type Overview = {
  total_overdue_display: string;
  recovered_display: string;
  recovery_rate_display: string;
  invoices_total: number;
  invoices_in_human_review: number;
  active_promises: number;
};
type QueueRow = {
  id: string;
  invoice_number: string;
  customer_name: string;
  outstanding_paise: number;
  status: string;
  days_overdue: number;
  tier_label: string;
  why: string;
  next_action: string;
  dispute_open: boolean;
};
type Exceptions = { total: number };

function money(paise: number) {
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(paise / 100);
}

export function LiveWorkspaceDashboard() {
  const [merchant, setMerchant] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [queue, setQueue] = useState<QueueRow[]>([]);
  const [exceptions, setExceptions] = useState<Exceptions | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const id = window.localStorage.getItem("vasooli_live_merchant") || "";
    Promise.resolve().then(() => {
      setMerchant(id);
      if (!id) return;
      Promise.all([
      liveGet<Overview>("/api/live/workspace/overview", id),
      liveGet<QueueRow[]>("/api/live/workspace/queue?limit=8", id),
      liveGet<Exceptions>("/api/live/workspace/exceptions", id),
      ]).then(([metrics, rows, failed]) => {
        setOverview(metrics);
        setQueue(rows);
        setExceptions(failed);
      }).catch((cause) => setError(cause instanceof Error ? cause.message : "Unable to load workspace"));
    });
  }, []);

  if (!merchant) return <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6"><LiveSignInPrompt what="Your recovery workspace" /></main>;
  return <main className="mx-auto max-w-7xl px-4 py-7 sm:px-6 sm:py-9">
    <div className="flex flex-wrap items-end justify-between gap-4 border-b border-line pb-6">
      <div><p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">Live recovery desk</p><h1 className="mt-2 text-3xl font-semibold tracking-tight">Know what moved, what stopped, and what needs you.</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-ink-3">Recovery stops when money lands, pauses on a promise, and routes disputes to a person.</p></div>
      <div className="flex gap-2"><Link href="/live/invoices#import" className="rounded-lg border border-line px-4 py-2 text-sm">Import CSV</Link><Link href="/live/readiness" className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white">Check readiness</Link></div>
    </div>
    {error ? <p role="alert" className="mt-5 rounded-xl bg-rose-500/10 p-4 text-sm text-rose-700">{error}</p> : null}
    {overview ? <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {[["Outstanding", overview.total_overdue_display], ["Recovered · 30d", overview.recovered_display], ["Recovery rate", overview.recovery_rate_display], ["Invoices", String(overview.invoices_total)]].map(([label, value]) => <div key={label} className="rounded-2xl border border-line bg-panel p-5"><p className="text-xs uppercase tracking-wider text-ink-4">{label}</p><p className="mt-2 text-2xl font-semibold">{value}</p></div>)}
    </div> : <p className="mt-8 text-sm text-ink-4">Loading live metrics…</p>}
    <div className="mt-6 grid gap-4 lg:grid-cols-[1fr_280px]">
      <section className="overflow-hidden rounded-2xl border border-line bg-panel">
        <div className="flex items-center justify-between border-b border-line px-5 py-4"><div><h2 className="font-semibold">Recovery queue</h2><p className="text-xs text-ink-4">Highest outstanding value first</p></div><Link href="/live/invoices" className="text-xs font-semibold text-accent">View all →</Link></div>
        {queue.length ? queue.map((row) => <Link href={`/live/invoices/${row.id}`} key={row.id} className="grid gap-2 border-b border-line px-5 py-4 transition last:border-0 hover:bg-panel-2 sm:grid-cols-[1.1fr_.8fr_.7fr]">
          <div><p className="text-sm font-semibold">{row.invoice_number} · {row.customer_name}</p><p className="mt-1 text-xs text-ink-4">{row.why}</p></div><div><p className="text-sm font-medium">{money(row.outstanding_paise)}</p><p className="text-xs text-ink-4">{row.days_overdue} days overdue</p></div><div className="sm:text-right"><p className={row.dispute_open ? "text-sm text-amber-600" : "text-sm capitalize text-ink-3"}>{row.dispute_open ? "Dispute open" : row.status.replaceAll("_", " ")}</p><p className="text-xs text-ink-4">{row.next_action}</p></div>
        </Link>) : <p className="p-8 text-sm text-ink-4">No invoices are waiting for recovery.</p>}
      </section>
      <aside className="space-y-3">
        <Link href="/live/disputes" className="block rounded-2xl border border-line bg-panel p-5"><p className="text-xs uppercase tracking-wider text-ink-4">Human review</p><p className="mt-2 text-3xl font-semibold">{overview?.invoices_in_human_review ?? "—"}</p><p className="mt-1 text-xs text-ink-3">Invoices paused for a person</p></Link>
        <Link href="/live/promises" className="block rounded-2xl border border-line bg-panel p-5"><p className="text-xs uppercase tracking-wider text-ink-4">Active promises</p><p className="mt-2 text-3xl font-semibold">{overview?.active_promises ?? "—"}</p><p className="mt-1 text-xs text-ink-3">Recovery paused until promised date</p></Link>
        <Link href="/live/exceptions" className="block rounded-2xl border border-line bg-panel p-5"><p className="text-xs uppercase tracking-wider text-ink-4">Exceptions</p><p className="mt-2 text-3xl font-semibold">{exceptions?.total ?? "—"}</p><p className="mt-1 text-xs text-ink-3">Delivery, reply, and payment failures</p></Link>
      </aside>
    </div>
  </main>;
}
