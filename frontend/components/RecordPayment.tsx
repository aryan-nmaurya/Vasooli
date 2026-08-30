"use client";

/**
 * Money that reached the merchant without passing through a Vasooli payment link.
 *
 * The gap this closes is the one that made the whole product's central promise false:
 * Vasooli could only see payments made through links it created itself, so a B2B
 * customer who paid by NEFT stayed overdue in the queue and kept receiving reminders.
 * Chasing someone who has already paid is the single worst thing a collections system
 * can do, and no amount of webhook correctness prevented it.
 *
 * Two things this screen deliberately does NOT do:
 *
 * 1. It never shows one combined "paid" figure on its own. Razorpay-verified money and
 *    money a colleague typed in are different kinds of fact, and collapsing them is how
 *    an unverified claim starts reading like a settled payment.
 * 2. It offers no edit and no delete. A mistake is corrected by reversing the entry,
 *    which leaves both the claim and the retraction visible.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

import type { ExternalPayment, PaymentMethodOption } from "@/lib/api";

const FALLBACK_METHODS: PaymentMethodOption[] = [
  { value: "bank_transfer", label: "Bank transfer (NEFT / RTGS / IMPS)" },
  { value: "upi", label: "UPI" },
  { value: "cheque", label: "Cheque" },
  { value: "cash", label: "Cash" },
  { value: "razorpay_unlinked", label: "Razorpay payment outside this link" },
  { value: "adjustment", label: "Credit note or agreed adjustment" },
];

/**
 * Rupees in the field, paise on the wire.
 *
 * The API takes integer paise and nothing else — a float amount is how ₹1 goes missing
 * between two languages. Parsed here rather than sent as typed so the rounding happens
 * once, visibly, at the boundary.
 */
function toPaise(rupees: string): number | null {
  const cleaned = rupees.replace(/[,\s₹]/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return null;
  return Math.round(Number(cleaned) * 100);
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-ink-2">{label}</span>
      {children}
      {hint ? <span className="mt-1 block text-[11px] text-ink-3">{hint}</span> : null}
    </label>
  );
}

const inputClass =
  "mt-1 w-full rounded-lg border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-ink-4";

export function RecordPayment({
  invoiceId,
  outstandingDisplay,
  linkPaidDisplay,
  externalPaidDisplay,
  payments,
  methods = FALLBACK_METHODS,
}: {
  invoiceId: string;
  outstandingDisplay: string;
  linkPaidDisplay: string;
  externalPaidDisplay: string;
  payments: ExternalPayment[];
  methods?: PaymentMethodOption[];
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState(methods[0]?.value ?? "bank_transfer");
  const [reference, setReference] = useState("");
  const [receivedOn, setReceivedOn] = useState(() => new Date().toISOString().slice(0, 10));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const paise = toPaise(amount);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (paise === null) {
      setError("Enter an amount in rupees, for example 42000 or 42000.50.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: `/api/dashboard/invoices/${invoiceId}/payments`,
          body: {
            amount_paise: paise,
            method,
            reference: reference.trim(),
            received_on: receivedOn,
            note,
          },
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        // The backend refuses a duplicate reference with a 409 and an explanation
        // naming the invoice. Showing it verbatim is more use than "something failed".
        setError(data.detail ?? data.error ?? "Could not record this payment.");
        return;
      }
      setOpen(false);
      setAmount("");
      setReference("");
      setNote("");
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-line bg-panel px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-ink">Payments received</h2>
          <p className="mt-1 max-w-prose text-xs text-ink-3">
            Vasooli reconciles its own payment links automatically. A bank transfer, UPI,
            cheque, or agreed adjustment has to be recorded here, or this customer keeps
            getting chased for money they have already sent.
          </p>
        </div>
        <button
          onClick={() => setOpen((v) => !v)}
          className="shrink-0 rounded-md bg-invert px-3 py-1.5 text-xs font-medium text-invert-ink transition hover:opacity-90"
        >
          {open ? "Cancel" : "Record a payment"}
        </button>
      </div>

      {/* Split, always. Which half of the balance a provider verified is not a
          detail — it is the difference between evidence and someone's word. */}
      <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-line pt-3 text-sm">
        <div>
          <dt className="text-xs uppercase tracking-wider text-ink-3">Razorpay verified</dt>
          <dd className="mt-0.5 font-semibold tabular-nums text-ink">{linkPaidDisplay}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wider text-ink-3">Recorded by hand</dt>
          <dd className="mt-0.5 font-semibold tabular-nums text-ink">{externalPaidDisplay}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wider text-ink-3">Still outstanding</dt>
          <dd className="mt-0.5 font-semibold tabular-nums text-ink">{outstandingDisplay}</dd>
        </div>
      </dl>

      {open ? (
        <form onSubmit={submit} className="mt-4 space-y-3 border-t border-line pt-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Amount received" hint="In rupees. Stored as integer paise.">
              <input
                autoFocus
                inputMode="decimal"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="42000"
                className={inputClass}
              />
            </Field>
            <Field label="How it arrived">
              <select
                value={method}
                onChange={(e) => setMethod(e.target.value)}
                className={inputClass}
              >
                {methods.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="Reference"
              hint="UTR, cheque number, or payment id. Required — an entry nobody can trace back is an entry nobody can check."
            >
              <input
                value={reference}
                onChange={(e) => setReference(e.target.value)}
                placeholder="UTR230825001234"
                className={inputClass}
              />
            </Field>
            <Field label="Date on the statement" hint="Not the date you are typing this.">
              <input
                type="date"
                value={receivedOn}
                onChange={(e) => setReceivedOn(e.target.value)}
                className={inputClass}
              />
            </Field>
          </div>
          <Field label="Note (optional)">
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Confirmed against the 25 Aug bank statement"
              className={inputClass}
            />
          </Field>

          <p className="text-[11px] leading-relaxed text-ink-3">
            This is recorded as your assertion, under your name, not as a verified
            payment. If it settles the invoice, recovery stops and the payment link is
            closed.
          </p>

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={busy || !amount || !reference.trim()}
              className="rounded-md bg-invert px-3 py-2 text-sm font-medium text-invert-ink transition hover:opacity-90 disabled:opacity-50"
            >
              {busy ? "Recording…" : "Record payment"}
            </button>
            {error ? (
              <span className="text-xs text-rose-700 dark:text-rose-300">{error}</span>
            ) : null}
          </div>
        </form>
      ) : null}

      {payments.length > 0 ? (
        <ul className="mt-4 divide-y divide-line-2 border-t border-line">
          {payments.map((payment) => (
            <PaymentRow key={payment.id} payment={payment} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function PaymentRow({ payment }: { payment: ExternalPayment }) {
  const router = useRouter();
  const [reversing, setReversing] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function reverse() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: `/api/dashboard/payments/${payment.id}/reverse`,
          body: { reason },
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail ?? "Could not reverse this entry.");
        return;
      }
      setReversing(false);
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className={`py-3 text-sm ${payment.active ? "" : "opacity-60"}`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span
          className={`font-semibold tabular-nums text-ink ${
            payment.active ? "" : "line-through"
          }`}
        >
          {payment.amount_display}
        </span>
        <span className="text-ink-2">{payment.method_label}</span>
        <span className="font-mono text-[11px] text-ink-3">{payment.reference}</span>
        <span className="text-xs text-ink-3">received {payment.received_on}</span>
        <span className="ml-auto text-xs text-ink-3">
          by {payment.recorded_by.replace(/^human:/, "")}
        </span>
      </div>

      {payment.note ? <p className="mt-1 text-xs text-ink-3">{payment.note}</p> : null}

      {payment.active ? (
        reversing ? (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              autoFocus
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why is this being reversed?"
              className="flex-1 rounded-md border border-line bg-panel px-2 py-1 text-xs text-ink outline-none focus:border-ink-4"
            />
            <button
              onClick={reverse}
              disabled={busy || !reason.trim()}
              className="rounded-md px-2.5 py-1 text-xs text-rose-700 ring-1 ring-inset ring-rose-200 transition hover:bg-rose-50 disabled:opacity-50 dark:text-rose-300 dark:ring-rose-500/30 dark:hover:bg-rose-500/10"
            >
              {busy ? "Reversing…" : "Confirm reversal"}
            </button>
            <button
              onClick={() => setReversing(false)}
              className="text-xs text-ink-3 hover:text-ink-2"
            >
              Cancel
            </button>
            {error ? (
              <span className="text-xs text-rose-700 dark:text-rose-300">{error}</span>
            ) : null}
          </div>
        ) : (
          <button
            onClick={() => setReversing(true)}
            className="mt-1 text-xs text-ink-3 underline-offset-2 hover:text-ink-2 hover:underline"
          >
            Reverse this entry
          </button>
        )
      ) : (
        <p className="mt-1 text-xs text-ink-3">
          Reversed by {payment.reversed_by?.replace(/^human:/, "") ?? "—"} — “
          {payment.reversal_reason}”. The entry is kept; the balance was recomputed
          without it.
        </p>
      )}
    </li>
  );
}
