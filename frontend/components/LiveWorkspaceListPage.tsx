"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { LiveSignInPrompt } from "@/components/LiveSignInPrompt";
import { ProvenanceBadge, StatusBadge } from "@/components/badges";
import { liveGet, livePost } from "@/lib/live-api";

type Kind = "promises" | "disputes" | "exceptions" | "audit";
type Row = Record<string, unknown>;

const COPY: Record<Kind, [string, string, string]> = {
  promises: ["Commitments", "Promise tracker", "A broken promise resumes escalation at the tier it paused, never back at polite."],
  disputes: ["Human review", "Dispute review", "Customer objections pause recovery. A person checks the evidence before anything resumes."],
  exceptions: ["Operations", "Exception queue", "Payment, delivery, link-closure, and reply failures that automation could not resolve."],
  audit: ["Activity", "Audit log", "Append-only. Every policy decision, message, human action, and payment event remains attributable."],
};

export function LiveWorkspaceListPage({ kind }: { kind: Kind }) {
  const [merchant, setMerchant] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [groups, setGroups] = useState<Record<string, Row[]> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback((id: string) => liveGet<Row[] | (Record<string, Row[]> & { total: number })>(`/api/live/workspace/${kind}`, id).then((body) => { if (Array.isArray(body)) { setRows(body); setGroups(null); } else { setGroups(body); setRows([]); } }), [kind]);

  useEffect(() => {
    const id = window.localStorage.getItem("vasooli_live_merchant") || "";
    Promise.resolve().then(() => {
      setMerchant(id);
      if (id) load(id).catch((cause) => setError(cause instanceof Error ? cause.message : "Unable to load workspace"));
    });
  }, [load]);

  async function retry(group: string, row: Row) {
    const paths: Record<string, string> = { reconciliation: `events/${row.event_id}/retry`, inbound: `inbound/${row.id}/retry` };
    if (!paths[group]) return;
    try { await livePost(`/api/live/workspace/exceptions/${paths[group]}`, merchant); await load(merchant); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Retry failed"); }
  }

  if (!merchant) return <LiveSignInPrompt what={COPY[kind][1]} />;
  return <div className="space-y-6">
    <PageHeading kind={kind} />
    {error ? <p role="alert" className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-700 dark:text-rose-300">{error}</p> : null}
    {kind === "promises" ? <PromisesView rows={rows} /> : null}
    {kind === "audit" ? <AuditView rows={rows} /> : null}
    {kind === "disputes" ? <DisputesView rows={rows} /> : null}
    {kind === "exceptions" ? <ExceptionsView groups={groups ?? {}} retry={retry} /> : null}
  </div>;
}

function PageHeading({ kind }: { kind: Kind }) {
  return <div><p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">{COPY[kind][0]}</p><h1 className="text-2xl font-semibold tracking-[-0.03em] text-ink sm:text-[1.75rem]">{COPY[kind][1]}</h1><p className="mt-1.5 max-w-3xl text-sm leading-6 text-ink-3">{COPY[kind][2]}</p></div>;
}

function PromisesView({ rows }: { rows: Row[] }) {
  const active = rows.filter((row) => row.status === "active").length;
  const kept = rows.filter((row) => row.status === "kept").length;
  const broken = rows.filter((row) => row.status === "broken").length;
  return <><div className="grid grid-cols-1 gap-3 sm:grid-cols-3"><Stat label="Active" value={active} /><Stat label="Kept" value={kept} tone="good" /><Stat label="Broken" value={broken} tone="bad" /></div><div className="scroll-x rounded-xl border border-line bg-panel shadow-sm"><table className="w-full min-w-[760px] text-sm"><thead className="border-b border-line text-left text-xs uppercase tracking-wider text-ink-3"><tr><th className="px-4 py-2.5 font-medium">Invoice</th><th className="px-4 py-2.5 font-medium">Customer</th><th className="px-4 py-2.5 font-medium">Promised by</th><th className="px-4 py-2.5 text-right font-medium">Amount</th><th className="px-4 py-2.5 font-medium">Status</th><th className="px-4 py-2.5 font-medium">Resumes at</th><th className="px-4 py-2.5 font-medium">Their words</th></tr></thead><tbody className="divide-y divide-line-2">{rows.map((row) => <tr key={String(row.id)} className="hover:bg-panel-2"><td className="px-4 py-2.5 font-mono text-[13px] text-accent">{String(row.invoice_number)}</td><td className="px-4 py-2.5 text-ink-2">{String(row.customer_name)}</td><td className="px-4 py-2.5 tabular-nums text-ink-2">{String(row.promised_date)}</td><td className="px-4 py-2.5 text-right tabular-nums text-ink">{String(row.amount_display)}</td><td className="px-4 py-2.5"><StatusBadge status={String(row.status)} /></td><td className="px-4 py-2.5 text-ink-3">Tier {String(row.tier_at_pause)}</td><td className="max-w-[280px] truncate px-4 py-2.5 text-xs italic text-ink-3">“{String(row.excerpt)}”</td></tr>)}{!rows.length ? <EmptyRow columns={7} message="No promises yet." /> : null}</tbody></table></div></>;
}

function AuditView({ rows }: { rows: Row[] }) {
  return <div className="scroll-x rounded-xl border border-line bg-panel shadow-sm"><table className="w-full min-w-[820px] text-sm"><thead className="border-b border-line text-left text-xs uppercase tracking-wider text-ink-3"><tr><th className="px-4 py-2.5 font-medium">When</th><th className="px-4 py-2.5 font-medium">By</th><th className="px-4 py-2.5 font-medium">Invoice</th><th className="px-4 py-2.5 font-medium">What happened</th></tr></thead><tbody className="divide-y divide-line-2">{rows.map((row, index) => <tr key={`${String(row.at)}-${index}`} className="hover:bg-panel-2"><td className="whitespace-nowrap px-4 py-2 font-mono text-[11px] text-ink-4">{new Date(String(row.at)).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" })}</td><td className="px-4 py-2"><ProvenanceBadge provenance={String(row.provenance) as "ai" | "policy" | "razorpay" | "system" | "human"} /></td><td className="px-4 py-2 font-mono text-[12px] text-accent">{row.invoice_number ? <Link href={`/live/invoices/${row.invoice_id}`}>{String(row.invoice_number)}</Link> : "—"}</td><td className="px-4 py-2 text-ink-2">{String(row.summary)}</td></tr>)}{!rows.length ? <EmptyRow columns={4} message="No audit events yet." /> : null}</tbody></table></div>;
}

function DisputesView({ rows }: { rows: Row[] }) {
  return <div className="grid gap-3">{rows.map((row) => <Link href={`/live/invoices/${row.invoice_id}`} key={String(row.case_id)} className="rounded-xl border border-line bg-panel px-5 py-4 transition hover:bg-panel-2"><div className="flex flex-wrap items-start justify-between gap-4"><div><p className="font-mono text-[13px] text-accent">{String(row.invoice_number)}</p><h2 className="mt-1 text-sm font-semibold text-ink">{String(row.customer_name)}</h2><p className="mt-1 max-w-3xl text-sm text-ink-3">{String(row.summary || row.reason)}</p></div><div className="text-right"><p className="font-semibold text-ink">{String(row.outstanding_display)}</p><p className="mt-1 text-xs text-ink-4">Confidence {String(row.confidence_display)}</p></div></div></Link>)}{!rows.length ? <p className="rounded-xl border border-line bg-panel px-4 py-10 text-center text-sm text-ink-3">No open disputes.</p> : null}</div>;
}

function ExceptionsView({ groups, retry }: { groups: Record<string, Row[]>; retry: (name: string, row: Row) => Promise<void> }) {
  return <div className="grid gap-4 sm:grid-cols-2">{Object.entries(groups).filter(([name]) => name !== "total").map(([name, items]) => <section key={name} className="overflow-hidden rounded-xl border border-line bg-panel"><div className="border-b border-line px-5 py-3"><h2 className="text-sm font-semibold capitalize">{name.replaceAll("_", " ")} · {items.length}</h2></div>{items.length ? items.map((row, index) => <div key={String(row.id || row.event_id || index)} className="flex items-start justify-between gap-4 border-b border-line-2 px-5 py-4 last:border-0"><div><p className="text-sm font-semibold">{String(row.invoice_number || row.event_id || "Operational event")}</p><p className="mt-1 text-xs leading-5 text-ink-3">{String(row.error || row.excerpt || "Needs attention")}</p><p className="mt-1 text-[11px] text-ink-4">Attempts: {String(row.attempts ?? 0)}{row.exhausted ? " · automatic retries exhausted" : ""}</p></div>{["reconciliation", "inbound"].includes(name) ? <button onClick={() => void retry(name, row)} className="shrink-0 rounded-lg border border-line px-3 py-2 text-xs hover:bg-panel-2">Retry now</button> : null}</div>) : <p className="p-5 text-sm text-ink-4">Nothing waiting here.</p>}</section>)}</div>;
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "good" | "bad" }) {
  return <div className="rounded-xl border border-line bg-panel px-5 py-4"><div className="text-xs uppercase tracking-wider text-ink-3">{label}</div><div className={`mt-1 text-2xl font-semibold tabular-nums ${tone === "good" ? "text-emerald-700 dark:text-emerald-300" : tone === "bad" ? "text-rose-700 dark:text-rose-300" : "text-ink"}`}>{value}</div></div>;
}

function EmptyRow({ columns, message }: { columns: number; message: string }) {
  return <tr><td colSpan={columns} className="px-4 py-10 text-center text-sm text-ink-3">{message}</td></tr>;
}
