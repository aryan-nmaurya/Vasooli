"use client";

/**
 * Feed a customer reply into the system from the dashboard.
 *
 * Runs the same extraction and promise-pausing path a real inbound email would, so
 * the demo shows the actual loop rather than a scripted shortcut.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

const PRESETS = [
  { label: "Promise to pay", body: "Cash is tight this month — I'll clear this by the 28th." },
  {
    label: "Complaint",
    body: "We were billed for 12 units but only received 9. Please check before we pay.",
  },
  { label: "Vague", body: "Thanks, noted. I'll look into it." },
];

export function SimulateReply({ invoiceId }: { invoiceId: string }) {
  const router = useRouter();
  const [body, setBody] = useState(PRESETS[0].body);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  async function send() {
    setBusy(true);
    setResult(null);
    try {
      const res = await fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: `/api/invoices/${invoiceId}/simulate-reply`,
          body: { body, use_llm: true },
        }),
      });
      const data = await res.json();
      setResult(data.note ?? data.error ?? "Done.");
      router.refresh();
    } catch {
      setResult("Request failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-line bg-panel px-4 py-4">
      <h2 className="text-sm font-semibold text-ink">Simulate a customer reply</h2>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {PRESETS.map((preset) => (
          <button
            key={preset.label}
            onClick={() => setBody(preset.body)}
            className="rounded-md px-2.5 py-1 text-xs text-ink-3 ring-1 ring-inset ring-line transition hover:text-ink"
          >
            {preset.label}
          </button>
        ))}
      </div>
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={3}
        className="mt-2.5 w-full resize-none rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-ink-4"
      />
      <div className="mt-2.5 flex items-center gap-3">
        <button
          onClick={send}
          disabled={busy}
          className="rounded-md bg-invert px-3 py-1.5 text-sm font-medium text-invert-ink transition hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Sending…" : "Send reply"}
        </button>
        {result ? <span className="text-xs text-ink-3">{result}</span> : null}
      </div>
    </div>
  );
}
