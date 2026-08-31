"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

import { LiveSignInPrompt } from "@/components/LiveSignInPrompt";
import { liveDownload, liveGet, liveUpload } from "@/lib/live-api";

type QueueRow = { id: string; invoice_number: string; customer_name: string; outstanding_paise: number; days_overdue: number; status: string; tier_label: string; why: string; next_action: string; dispute_open: boolean };
type Preview = { dry_run: boolean; parsed: number; would_import: number; duplicates: string[]; problems: { line: number; invoice_number: string; message: string }[]; result?: { ingested: number; skipped_duplicates: number; failed: number } };

function money(paise: number) { return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(paise / 100); }

export default function LiveInvoicesPage() {
  const [merchant, setMerchant] = useState("");
  const [rows, setRows] = useState<QueueRow[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = (id: string) => liveGet<QueueRow[]>("/api/live/workspace/queue", id).then(setRows);
  useEffect(() => { const id = window.localStorage.getItem("vasooli_live_merchant") || ""; Promise.resolve().then(() => { setMerchant(id); if (id) liveGet<QueueRow[]>("/api/live/workspace/queue", id).then(setRows).catch((cause) => setError(cause instanceof Error ? cause.message : "Unable to load invoices")); }); }, []);

  async function downloadTemplate() { try { const blob = await liveDownload("/api/live/invoices/csv/template", merchant); const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = "vasooli-import-template.csv"; anchor.click(); URL.revokeObjectURL(url); } catch (cause) { setError(cause instanceof Error ? cause.message : "Download failed"); } }

  async function upload(event: FormEvent, dryRun: boolean) {
    event.preventDefault();
    if (!file || !merchant) return;
    setBusy(true); setError(null);
    const data = new FormData(); data.set("file", file); data.set("dry_run", String(dryRun));
    try { const result = await liveUpload<Preview>("/api/live/invoices/csv/import", merchant, data); setPreview(result); if (!dryRun) await load(merchant); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Import failed"); }
    finally { setBusy(false); }
  }

  if (!merchant) return <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6"><LiveSignInPrompt what="Your invoice ledger" /></main>;
  return <main className="mx-auto max-w-7xl px-4 py-7 sm:px-6 sm:py-9">
    <div className="flex flex-wrap items-end justify-between gap-4"><div><p className="text-xs font-semibold uppercase tracking-wider text-accent">Recovery queue</p><h1 className="mt-2 text-3xl font-semibold">Invoices that need attention</h1><p className="mt-2 text-sm text-ink-3">Open an invoice for its conversation, payments, promises, disputes, and audit trail.</p></div><button type="button" onClick={downloadTemplate} className="rounded-lg border border-line px-4 py-2 text-sm">Download CSV template</button></div>
    <section id="import" className="mt-6 rounded-2xl border border-line bg-panel p-5">
      <form onSubmit={(event) => upload(event, true)} className="flex flex-col gap-4 sm:flex-row sm:items-end"><label className="flex-1 text-sm font-medium">Import invoices from CSV<span className="mt-1 block text-xs font-normal text-ink-4">Preview validates every row and names duplicates before anything is written.</span><input type="file" accept=".csv,text/csv" required onChange={(event) => { setFile(event.target.files?.[0] ?? null); setPreview(null); }} className="mt-3 block w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm" /></label><button disabled={busy || !file} className="rounded-lg bg-accent px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-50">{busy ? "Checking…" : "Preview import"}</button></form>
      {preview ? <div className="mt-4 rounded-xl border border-line bg-surface p-4 text-sm"><p><strong>{preview.parsed}</strong> valid rows · <strong>{preview.would_import}</strong> new · <strong>{preview.duplicates.length}</strong> duplicates</p>{preview.problems.length ? <ul className="mt-3 space-y-1 text-rose-700">{preview.problems.map((problem) => <li key={`${problem.line}-${problem.invoice_number}`}>Line {problem.line}, {problem.invoice_number}: {problem.message}</li>)}</ul> : <p className="mt-2 text-emerald-700">All parsed rows are valid.</p>}{preview.dry_run && preview.would_import ? <button type="button" disabled={busy} onClick={(event) => upload(event, false)} className="mt-4 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white">Confirm {preview.would_import} invoice{preview.would_import === 1 ? "" : "s"}</button> : null}{preview.result ? <p className="mt-3 text-emerald-700">Imported {preview.result.ingested}; skipped {preview.result.skipped_duplicates}; failed {preview.result.failed}.</p> : null}</div> : null}
    </section>
    {error ? <p role="alert" className="mt-4 rounded-xl bg-rose-500/10 p-4 text-sm text-rose-700">{error}</p> : null}
    <section className="mt-6 overflow-hidden rounded-2xl border border-line bg-panel"><div className="hidden grid-cols-[1fr_1fr_.7fr_.7fr] gap-4 border-b border-line px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-4 sm:grid"><span>Invoice</span><span>Why / next</span><span>Outstanding</span><span>Status</span></div>{rows.length ? rows.map((row) => <Link href={`/live/invoices/${row.id}`} key={row.id} className="grid gap-3 border-b border-line px-5 py-4 transition last:border-0 hover:bg-panel-2 sm:grid-cols-[1fr_1fr_.7fr_.7fr]"><div><p className="font-semibold">{row.invoice_number}</p><p className="text-xs text-ink-4">{row.customer_name} · {row.days_overdue} days overdue</p></div><div><p className="text-sm">{row.why}</p><p className="text-xs text-ink-4">{row.next_action}</p></div><p className="text-sm font-semibold">{money(row.outstanding_paise)}</p><div><p className={row.dispute_open ? "text-sm text-amber-600" : "text-sm capitalize"}>{row.dispute_open ? "Dispute open" : row.status.replaceAll("_", " ")}</p><p className="text-xs text-ink-4">{row.tier_label}</p></div></Link>) : <p className="p-8 text-sm text-ink-4">No invoices loaded. Import a CSV or sync your ERP.</p>}</section>
  </main>;
}
