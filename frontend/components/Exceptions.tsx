"use client";

/**
 * Operational exceptions — things that failed and need a person.
 *
 * Two separate queues because they mean different things to a finance operator:
 * money that arrived but could not be matched, and messages that never reached a
 * customer. Both were previously invisible outside application logs.
 *
 * Deliberately small. This is a queue with a retry button, not an admin panel.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

import type { Exceptions } from "@/lib/api";

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function RetryButton({ path, label = "Retry" }: { path: string; label?: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  async function retry() {
    setBusy(true);
    setResult(null);
    try {
      const res = await fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      const data = await res.json();
      setResult(data.recovered ? "recovered" : (data.error ?? "still failing"));
      router.refresh();
    } catch {
      setResult("request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="flex items-center gap-2">
      <button
        onClick={retry}
        disabled={busy}
        className="rounded-md px-2.5 py-1 text-xs text-ink-2 ring-1 ring-inset ring-line transition hover:bg-panel-2 hover:text-ink disabled:opacity-50"
      >
        {busy ? "Retrying…" : label}
      </button>
      {result ? (
        <span
          className={
            result === "recovered"
              ? "text-xs text-emerald-700 dark:text-emerald-300"
              : "text-xs text-ink-3"
          }
        >
          {result}
        </span>
      ) : null}
    </span>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-4 py-6 text-center text-sm text-ink-3">{children}</p>
  );
}

export function ExceptionsPanel({ data }: { data: Exceptions }) {
  if (data.total === 0) {
    return (
      <section className="rounded-xl border border-line bg-panel px-5 py-4">
        <h2 className="text-sm font-semibold text-ink">Operational exceptions</h2>
        <Empty>
          Nothing needs attention. Failed payments and undelivered reminders appear here.
        </Empty>
      </section>
    );
  }

  return (
    <section className="space-y-4">
      <h2 className="text-sm font-semibold text-ink">
        Operational exceptions
        <span className="ml-2 rounded-md bg-rose-50 px-2 py-0.5 text-xs font-medium text-rose-700 ring-1 ring-inset ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30">
          {data.total}
        </span>
      </h2>

      {data.reconciliation.length > 0 ? (
        <div className="rounded-xl border border-line">
          <div className="border-b border-line px-4 py-2.5">
            <span className="text-xs font-medium uppercase tracking-wider text-ink-3">
              Reconciliation — money received, not matched
            </span>
          </div>
          <div className="scroll-x">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="border-b border-line text-left text-xs uppercase tracking-wider text-ink-3">
                <tr>
                  <th className="px-4 py-2 font-medium">Event</th>
                  <th className="px-4 py-2 font-medium">Invoice</th>
                  <th className="px-4 py-2 font-medium">Error</th>
                  <th className="px-4 py-2 text-right font-medium">Attempts</th>
                  <th className="px-4 py-2 font-medium">Last tried</th>
                  <th className="px-4 py-2 font-medium">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line-2">
                {data.reconciliation.map((row) => (
                  <tr key={row.id}>
                    <td className="px-4 py-2 font-mono text-[11px] text-ink-3">
                      {row.event_id}
                    </td>
                    <td className="px-4 py-2 font-mono text-[12px] text-accent">
                      {row.invoice_number ?? "unmatched"}
                    </td>
                    <td className="max-w-[240px] truncate px-4 py-2 text-xs text-rose-700 dark:text-rose-300">
                      {row.error}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums text-ink-2">
                      {row.attempts}
                      {row.exhausted ? (
                        <span className="ml-1 text-[10px] text-rose-700 dark:text-rose-300">
                          exhausted
                        </span>
                      ) : null}
                    </td>
                    <td className="px-4 py-2 text-xs text-ink-3">
                      {timeAgo(row.last_attempt_at)}
                    </td>
                    <td className="px-4 py-2">
                      <RetryButton
                        path={`/api/dashboard/exceptions/events/${row.event_id}/retry`}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {data.communication.length > 0 ? (
        <div className="rounded-xl border border-line">
          <div className="border-b border-line px-4 py-2.5">
            <span className="text-xs font-medium uppercase tracking-wider text-ink-3">
              Communication — reminders that never arrived
            </span>
          </div>
          <div className="scroll-x">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="border-b border-line text-left text-xs uppercase tracking-wider text-ink-3">
                <tr>
                  <th className="px-4 py-2 font-medium">Invoice</th>
                  <th className="px-4 py-2 font-medium">Customer</th>
                  <th className="px-4 py-2 font-medium">Tier</th>
                  <th className="px-4 py-2 font-medium">Error</th>
                  <th className="px-4 py-2 text-right font-medium">Attempts</th>
                  <th className="px-4 py-2 font-medium">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line-2">
                {data.communication.map((row) => (
                  <tr key={row.id}>
                    <td className="px-4 py-2 font-mono text-[12px] text-accent">
                      {row.invoice_number}
                    </td>
                    <td className="px-4 py-2 text-ink-2">{row.customer_name}</td>
                    <td className="px-4 py-2 text-ink-2">
                      {row.tier} · {row.tone}
                    </td>
                    <td className="max-w-[240px] truncate px-4 py-2 text-xs text-rose-700 dark:text-rose-300">
                      {row.error ?? "not yet attempted"}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums text-ink-2">
                      {row.attempts}
                      {row.exhausted ? (
                        <span className="ml-1 text-[10px] text-rose-700 dark:text-rose-300">
                          exhausted
                        </span>
                      ) : null}
                    </td>
                    <td className="px-4 py-2">
                      <RetryButton
                        path={`/api/dashboard/exceptions/reminders/${row.id}/retry`}
                        label="Resend"
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="border-t border-line px-4 py-2 text-xs text-ink-3">
            A failed delivery does <strong className="text-ink-2">not</strong> count as a sent
            reminder. These tiers are still owed to the customer.
          </p>
        </div>
      ) : null}

      {data.unclosed_links.length > 0 ? (
        <div className="rounded-xl border border-line">
          <div className="border-b border-line px-4 py-2.5">
            <span className="text-xs font-medium uppercase tracking-wider text-ink-3">
              Payment links still open on recovered invoices
            </span>
          </div>
          <ul className="divide-y divide-line-2">
            {data.unclosed_links.map((row) => (
              <li key={row.id} className="flex items-center gap-4 px-4 py-2 text-sm">
                <span className="font-mono text-[12px] text-accent">{row.invoice_number}</span>
                <span className="font-mono text-[11px] text-ink-3">{row.payment_link_id}</span>
                <span className="truncate text-xs text-rose-700 dark:text-rose-300">
                  {row.error}
                </span>
                <span className="ml-auto text-xs text-ink-3">{row.attempts} attempts</span>
              </li>
            ))}
          </ul>
          <p className="border-t border-line px-4 py-2 text-xs text-ink-3">
            The payment is recorded. These links can still take a second payment until closed.
          </p>
        </div>
      ) : null}
    </section>
  );
}
