"use client";

/**
 * Operational exceptions — things that failed and need a person.
 *
 * Four separate queues, because they mean different things to a finance operator:
 * money that arrived but could not be matched, reminders that never reached a
 * customer, payment links still open on invoices that are settled, and customer
 * replies that were received but could not be interpreted.
 *
 * A retry button is not enough for all of them, and pretending otherwise was the
 * audit's complaint. Retrying an unmatched payment cannot conjure a payment link that
 * was never in our database — that one needs a person to say which invoice it belongs
 * to, which is what the match control below does.
 *
 * Deliberately small. This is a work queue, not an admin panel.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

import type { Exceptions, QueueRow } from "@/lib/api";

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

/**
 * Assign an unmatched Razorpay settlement to an invoice.
 *
 * The backend deliberately does not let the operator type the amount: they decide
 * WHICH invoice the money belongs to, and the figure comes from the stored payload.
 */
function MatchControl({ eventId, invoices }: { eventId: string; invoices: QueueRow[] }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [invoiceId, setInvoiceId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function match() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: `/api/dashboard/exceptions/events/${eventId}/match`,
          body: { invoice_id: invoiceId },
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail ?? "Could not match this payment.");
        return;
      }
      setOpen(false);
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="rounded-md px-2.5 py-1 text-xs text-ink-2 ring-1 ring-inset ring-line transition hover:bg-panel-2 hover:text-ink"
      >
        Match to invoice
      </button>
    );
  }

  return (
    <span className="flex flex-wrap items-center gap-2">
      <select
        autoFocus
        value={invoiceId}
        onChange={(e) => setInvoiceId(e.target.value)}
        className="rounded-md border border-line bg-panel px-2 py-1 text-xs text-ink outline-none focus:border-ink-4"
      >
        <option value="">Choose an invoice…</option>
        {invoices.map((row) => (
          <option key={row.id} value={row.id}>
            {row.invoice_number} · {row.customer_name} · {row.amount_display}
          </option>
        ))}
      </select>
      <button
        onClick={match}
        disabled={busy || !invoiceId}
        className="rounded-md bg-invert px-2.5 py-1 text-xs font-medium text-invert-ink transition hover:opacity-90 disabled:opacity-50"
      >
        {busy ? "Matching…" : "Confirm"}
      </button>
      <button onClick={() => setOpen(false)} className="text-xs text-ink-3 hover:text-ink-2">
        Cancel
      </button>
      {error ? <span className="text-xs text-rose-700 dark:text-rose-300">{error}</span> : null}
    </span>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-4 py-6 text-center text-sm text-ink-3">{children}</p>
  );
}

export function ExceptionsPanel({
  data,
  invoices = [],
}: {
  data: Exceptions;
  /** Candidates for manual matching. Empty simply hides the match control. */
  invoices?: QueueRow[];
}) {
  //: Items automatic retry has given up on. These will sit there silently forever
  //: unless someone is told, which is the whole point of surfacing them.
  const exhausted =
    data.reconciliation.filter((r) => r.exhausted).length +
    data.communication.filter((r) => r.exhausted).length +
    (data.inbound ?? []).filter((r) => r.exhausted).length;

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
      {exhausted > 0 ? (
        <div
          role="alert"
          className="rounded-xl border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200"
        >
          <strong className="font-semibold">
            {exhausted} exception{exhausted === 1 ? "" : "s"} out of automatic retries.
          </strong>{" "}
          Vasooli has stopped retrying {exhausted === 1 ? "it" : "these"} and will not
          try again on its own. A person needs to look.
        </div>
      ) : null}

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
                      <span className="flex flex-wrap items-center gap-2">
                        <RetryButton
                          path={`/api/dashboard/exceptions/events/${row.event_id}/retry`}
                        />
                        {/* Retrying an unmatched payment cannot find an invoice that
                            was never in our database. This one needs a person. */}
                        {row.invoice_number === null && invoices.length > 0 ? (
                          <MatchControl eventId={row.event_id} invoices={invoices} />
                        ) : null}
                      </span>
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

      {(data.inbound ?? []).length > 0 ? (
        <div className="rounded-xl border border-line">
          <div className="border-b border-line px-4 py-2.5">
            <span className="text-xs font-medium uppercase tracking-wider text-ink-3">
              Customer replies received but not understood
            </span>
          </div>
          <ul className="divide-y divide-line-2">
            {data.inbound.map((row) => (
              <li key={row.id} className="px-4 py-3 text-sm">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="font-mono text-[12px] text-accent">
                    {row.invoice_number}
                  </span>
                  <span className="text-ink-2">{row.sender}</span>
                  <span className="text-xs text-ink-3">{timeAgo(row.received_at)}</span>
                  <span className="ml-auto flex items-center gap-2 text-xs text-ink-3">
                    {row.attempts} attempt{row.attempts === 1 ? "" : "s"}
                    {row.exhausted ? (
                      <span className="text-[10px] text-rose-700 dark:text-rose-300">
                        exhausted
                      </span>
                    ) : null}
                    <RetryButton
                      path={`/api/dashboard/exceptions/inbound/${row.id}/retry`}
                      label="Reprocess"
                    />
                  </span>
                </div>
                <p className="mt-1 line-clamp-2 text-xs text-ink-2">“{row.excerpt}”</p>
                <p className="mt-1 text-xs text-rose-700 dark:text-rose-300">{row.error}</p>
              </li>
            ))}
          </ul>
          <p className="border-t border-line px-4 py-2 text-xs text-ink-3">
            The message itself is stored and is evidence. What failed is reading it — so
            a promise or a dispute in one of these has{" "}
            <strong className="text-ink-2">not</strong> been acted on yet.
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
                <RetryButton
                  path={`/api/dashboard/exceptions/links/${row.id}/retry-closure`}
                  label="Retry closure"
                />
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
