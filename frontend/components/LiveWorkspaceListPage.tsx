"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { LiveSignInPrompt } from "@/components/LiveSignInPrompt";
import { liveGet, livePost } from "@/lib/live-api";

type Kind = "promises" | "disputes" | "exceptions" | "audit";
type Row = Record<string, unknown>;

const COPY: Record<Kind, [string, string]> = {
  promises: ["Promises to pay", "Recovery pauses until the promised date, then records whether the promise was kept or broken."],
  disputes: ["Dispute review", "Customer objections pause recovery. A person checks the evidence before anything resumes."],
  exceptions: ["Operations exceptions", "Payment, delivery, link-closure, and reply failures that automation could not resolve."],
  audit: ["Audit trail", "An append-only record of every policy decision, message, human action, and payment event."],
};

export function LiveWorkspaceListPage({ kind }: { kind: Kind }) {
  const [merchant, setMerchant] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [groups, setGroups] = useState<Record<string, Row[]> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = (id: string) => liveGet<Row[] | (Record<string, Row[]> & { total: number })>(`/api/live/workspace/${kind}`, id).then((body) => { if (Array.isArray(body)) { setRows(body); setGroups(null); } else { setGroups(body); setRows([]); } });
  useEffect(() => { const id = window.localStorage.getItem("vasooli_live_merchant") || ""; Promise.resolve().then(() => { setMerchant(id); if (id) liveGet<Row[] | (Record<string, Row[]> & { total: number })>(`/api/live/workspace/${kind}`, id).then((body) => { if (Array.isArray(body)) { setRows(body); setGroups(null); } else { setGroups(body); setRows([]); } }).catch((cause) => setError(cause instanceof Error ? cause.message : "Unable to load workspace")); }); }, [kind]);
  async function retry(group: string, row: Row) { const paths: Record<string, string> = { reconciliation: `events/${row.event_id}/retry`, inbound: `inbound/${row.id}/retry` }; if (!paths[group]) return; try { await livePost(`/api/live/workspace/exceptions/${paths[group]}`, merchant); await load(merchant); } catch (cause) { setError(cause instanceof Error ? cause.message : "Retry failed"); } }
  if (!merchant) return <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6"><LiveSignInPrompt what={COPY[kind][0]} /></main>;
  return <main className="mx-auto max-w-6xl px-4 py-7 sm:px-6 sm:py-9"><p className="text-xs font-semibold uppercase tracking-wider text-accent">Live recovery desk</p><h1 className="mt-2 text-3xl font-semibold">{COPY[kind][0]}</h1><p className="mt-2 max-w-3xl text-sm text-ink-3">{COPY[kind][1]}</p>{error ? <p role="alert" className="mt-5 rounded-xl bg-rose-500/10 p-4 text-sm text-rose-700">{error}</p> : null}{groups ? <div className="mt-6 space-y-5">{Object.entries(groups).filter(([name]) => name !== "total").map(([name, items]) => <section key={name} className="overflow-hidden rounded-2xl border border-line bg-panel"><h2 className="border-b border-line px-5 py-4 font-semibold capitalize">{name.replaceAll("_", " ")} · {items.length}</h2>{items.length ? items.map((row, index) => <div key={String(row.id || row.event_id || index)} className="flex items-start justify-between gap-4 border-b border-line px-5 py-4 last:border-0"><div><p className="text-sm font-semibold">{String(row.invoice_number || row.event_id || "Operational event")}</p><p className="mt-1 text-sm text-ink-3">{String(row.error || row.excerpt || "Needs attention")}</p><p className="mt-1 text-xs text-ink-4">Attempts: {String(row.attempts ?? 0)}{row.exhausted ? " · automatic retries exhausted" : ""}</p></div>{["reconciliation", "inbound"].includes(name) ? <button onClick={() => retry(name, row)} className="rounded-lg border border-line px-3 py-2 text-xs">Retry now</button> : null}</div>) : <p className="p-5 text-sm text-ink-4">Nothing waiting here.</p>}</section>)}</div> : <section className="mt-6 overflow-hidden rounded-2xl border border-line bg-panel">{rows.length ? rows.map((row, index) => <Link href={row.invoice_id ? `/live/invoices/${row.invoice_id}` : "#"} key={String(row.id || row.case_id || `${row.at}-${index}`)} className="block border-b border-line px-5 py-4 last:border-0 hover:bg-panel-2"><div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm font-semibold">{String(row.invoice_number || row.customer_name || row.action || "Event")}</p><p className="text-xs text-ink-4">{String(row.promised_date || row.opened_at || row.at || "")}</p></div><p className="mt-1 text-sm text-ink-3">{String(row.summary || row.reason || row.excerpt || row.status || "")}</p>{row.amount_display || row.outstanding_display ? <p className="mt-1 text-xs text-ink-4">{String(row.amount_display || row.outstanding_display)}</p> : null}</Link>) : <p className="p-8 text-sm text-ink-4">No records yet.</p>}</section>}</main>;
}
