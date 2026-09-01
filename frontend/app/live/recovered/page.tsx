"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { LiveSignInPrompt } from "@/components/LiveSignInPrompt";
import { liveGet } from "@/lib/live-api";

type RecoveredInvoice = {
  id: string;
  invoice_number: string;
  customer_name: string;
  amount_display: string;
  recovered_at: string | null;
  reason_category: string | null;
  why: string;
  status: string;
};

function when(value: string | null) {
  return value ? new Date(value).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "—";
}

export default function LiveRecoveredPage() {
  const [merchant, setMerchant] = useState("");
  const [rows, setRows] = useState<RecoveredInvoice[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const id = window.localStorage.getItem("vasooli_live_merchant") || "";
    Promise.resolve().then(() => {
      setMerchant(id);
      if (id) liveGet<RecoveredInvoice[]>("/api/live/workspace/queue?status=recovered&limit=500", id).then(setRows).catch((cause) => setError(cause instanceof Error ? cause.message : "Unable to load recovered invoices"));
    });
  }, []);

  if (!merchant) return <LiveSignInPrompt what="Recovered invoices" />;

  return <div className="flex flex-col gap-6">
    <div>
      <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">Collections</p>
      <h1 className="text-2xl font-semibold tracking-[-0.03em] text-ink sm:text-[1.75rem]">Recovered</h1>
      <p className="mt-1.5 text-sm leading-6 text-ink-3">{rows.length ? `${rows.length} invoice${rows.length === 1 ? "" : "s"} settled — confirmed from trusted payment records.` : "Nothing recovered yet."}</p>
    </div>
    {error ? <p role="alert" className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-700 dark:text-rose-300">{error}</p> : null}
    {rows.length ? <div className="scroll-x rounded-xl border border-line bg-panel shadow-sm"><table className="w-full min-w-[46rem] text-sm"><thead><tr className="border-b border-line text-left">{["Invoice", "Customer", "Amount", "Recovered", "Reason", ""].map((heading, index) => <th key={heading || index} className={`px-4 py-2.5 text-xs font-medium uppercase tracking-wider text-ink-3 ${heading === "Amount" ? "text-right" : ""}`}>{heading}</th>)}</tr></thead><tbody className="divide-y divide-line-2">{rows.map((row) => <tr key={row.id} className="transition hover:bg-panel-2"><td className="px-4 py-2.5"><Link href={`/live/invoices/${row.id}`} className="font-mono text-[13px] text-accent hover:underline">{row.invoice_number}</Link></td><td className="px-4 py-2.5 text-ink-2">{row.customer_name}</td><td className="px-4 py-2.5 text-right font-medium tabular-nums text-ink">{row.amount_display}</td><td className="px-4 py-2.5 tabular-nums text-ink-3">{when(row.recovered_at)}</td><td className="px-4 py-2.5 text-ink-3">{row.reason_category ?? "—"}</td><td className="max-w-[22rem] px-4 py-2.5 text-xs text-ink-3">{row.why}</td></tr>)}</tbody></table></div> : !error ? <p className="rounded-xl border border-line bg-panel px-4 py-10 text-center text-sm text-ink-3">When a payment is confirmed, the invoice closes and appears here.</p> : null}
  </div>;
}
