"use client";

/**
 * Create this invoice's payment link, for the case where provisioning was skipped or
 * failed. Idempotent on the server, so pressing it twice cannot produce two links.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

export function ProvisionButton({ invoiceId }: { invoiceId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function provision() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: `/api/invoices/${invoiceId}/provision` }),
      });
      const data = await res.json();
      if (!res.ok) setError(data.detail ?? data.error ?? "Failed.");
      router.refresh();
    } catch {
      setError("Request failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-line bg-panel px-5 py-4">
      <div className="text-xs uppercase tracking-wider text-ink-3">Payment link</div>
      <p className="mt-1 text-sm text-ink-3">
        No link yet — the customer has no way to pay this invoice.
      </p>
      <button
        onClick={provision}
        disabled={busy}
        className="mt-2.5 rounded-md bg-invert px-3 py-1.5 text-sm font-medium text-invert-ink transition hover:opacity-90 disabled:opacity-50"
      >
        {busy ? "Creating…" : "Create payment link"}
      </button>
      {error ? <p className="mt-2 text-xs text-rose-700 dark:text-rose-300">{error}</p> : null}
    </div>
  );
}
