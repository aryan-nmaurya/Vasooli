"use client";

/**
 * Trigger the recovery cycle from the dashboard.
 *
 * Calls the same endpoint the 10:00 scheduler calls, which calls the same function.
 * A demo button wired to its own code path would demonstrate the button.
 *
 * Dry run evaluates everything and sends nothing, so you can show what *would* happen
 * before committing to it — useful when live email is switched on.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

type Report = {
  sent: number;
  held: number;
  escalated: number;
  promises_broken: number;
  considered: number;
  errors: { invoice_number: string; error: string }[];
};

export function RunCycleButton() {
  const router = useRouter();
  const [busy, setBusy] = useState<null | "live" | "dry">(null);
  const [report, setReport] = useState<Report | null>(null);
  const [dry, setDry] = useState(false);

  async function run(dryRun: boolean) {
    setBusy(dryRun ? "dry" : "live");
    setReport(null);
    setDry(dryRun);
    try {
      const res = await fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: `/api/admin/run-cycle?dry_run=${dryRun}` }),
      });
      setReport(await res.json());
      router.refresh();
    } catch {
      setReport(null);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        onClick={() => run(false)}
        disabled={busy !== null}
        className="rounded-md bg-invert px-3 py-1.5 text-sm font-medium text-invert-ink transition hover:opacity-90 disabled:opacity-50"
      >
        {busy === "live" ? "Running…" : "Run recovery cycle"}
      </button>
      <button
        onClick={() => run(true)}
        disabled={busy !== null}
        className="rounded-md px-3 py-1.5 text-sm text-ink-3 ring-1 ring-inset ring-line transition hover:bg-panel-2 hover:text-ink disabled:opacity-50"
        title="Evaluate everything, send nothing"
      >
        {busy === "dry" ? "Checking…" : "Dry run"}
      </button>

      {report ? (
        <span className="text-xs text-ink-3">
          {dry ? "Would send" : "Sent"} <strong className="text-ink">{report.sent}</strong>
          {" · held "}
          <strong className="text-ink">{report.held}</strong>
          {" · escalated "}
          <strong className="text-ink">{report.escalated}</strong>
          {report.promises_broken ? ` · ${report.promises_broken} promise(s) broken` : ""}
          {report.errors?.length ? ` · ${report.errors.length} error(s)` : ""}
        </span>
      ) : null}
    </div>
  );
}
