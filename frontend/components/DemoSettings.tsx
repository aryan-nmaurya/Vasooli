"use client";

/**
 * Reviewer settings: time compression, and where reminder mail lands.
 *
 * The cadence fires at 3, 10 and 21 days overdue. That is the right schedule for a
 * real merchant and unwatchable in a demo, so this moves a simulated clock forward
 * and lets the ordinary recovery cycle react to the later date.
 *
 * It does not fabricate anything. Advancing calls the same `run_recovery_cycle` the
 * scheduler calls; whether a reminder goes out is still the policy engine's decision
 * against the same rules. Only the date the system believes it is has moved — which
 * is why the panel shows both dates side by side rather than hiding the difference.
 */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import type { DemoClock as Clock } from "@/lib/api";

type CycleReport = {
  considered: number;
  sent: number;
  held: number;
  escalated: number;
  promises_broken: number;
};

const STEP_CHOICES = [1, 3, 7, 14] as const;
const INTERVAL_CHOICES = [10, 20, 30, 60] as const;

/** How many advances an unattended auto-run may make before stopping itself. */
const MAX_AUTO_STEPS = 8;


/** "30 Aug 2026, 01:50" in IST — matches the backend's own strftime format exactly,
 * computed locally so the real-time field can tick without a network round trip. */
function formatIst(date: Date): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("day")} ${get("month")} ${get("year")}, ${get("hour")}:${get("minute")}`;
}

export function DemoSettingsPanel({ initial }: { initial: Clock }) {
  const router = useRouter();
  const [clock, setClock] = useState(initial);
  const [report, setReport] = useState<CycleReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [days, setDays] = useState<number>(7);
  const [intervalSeconds, setIntervalSeconds] = useState<number>(20);
  const [dryRun, setDryRun] = useState(false);
  const [auto, setAuto] = useState(false);
  const [stepsLeft, setStepsLeft] = useState(0);
  const [emailDraft, setEmailDraft] = useState("");
  const [emailSaving, setEmailSaving] = useState(false);
  const [emailNote, setEmailNote] = useState<string | null>(null);

  // The real-time field is just "what time is it right now" — no reason to wait on a
  // server round trip for that, and no reason for it to sit frozen at whatever it
  // read on the last click. Ticks locally; the simulated date is left untouched here
  // since it only moves through an actual advance.
  const [liveRealTime, setLiveRealTime] = useState(() => formatIst(new Date()));
  useEffect(() => {
    const id = setInterval(() => setLiveRealTime(formatIst(new Date())), 15_000);
    return () => clearInterval(id);
  }, []);

  async function post(path: string, body?: unknown) {
    const res = await fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, body }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail ?? data.error ?? "Request failed");
    return data;
  }

  async function step() {
    setBusy(true);
    setError(null);
    try {
      const data = await post("/api/demo/advance", {
        days,
        run_cycle: true,
        dry_run: dryRun,
      });
      setClock(data.clock);
      setReport(data.cycle);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not advance the clock.");
      setAuto(false);
    } finally {
      setBusy(false);
    }
  }

  async function saveEmail(address: string | null) {
    setEmailSaving(true);
    setEmailNote(null);
    setError(null);
    try {
      const data = await post("/api/demo/email-redirect", { address });
      setClock(data.clock);
      setEmailDraft("");
      setEmailNote(
        address ? `Reminders now go to ${data.clock.email_to}` : "Back to the default inbox",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update the address.");
    } finally {
      setEmailSaving(false);
    }
  }

  async function resetClock() {
    setBusy(true);
    setError(null);
    setAuto(false);
    try {
      const data = await post("/api/demo/reset");
      setClock(data.clock);
      setReport(null);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reset the clock.");
    } finally {
      setBusy(false);
    }
  }

  // Auto-advance. `stepsLeft` reaching zero is what ends the run, so the effect
  // only ever schedules — it never flips `auto` itself, which would cascade a
  // render from inside an effect body.
  //
  // Bounded by MAX_AUTO_STEPS so a tab left open overnight cannot walk the ledger
  // years into the future unattended.
  const autoRunning = auto && stepsLeft > 0;

  useEffect(() => {
    if (!autoRunning) return;
    const id = setTimeout(() => {
      void (async () => {
        await step();
        setStepsLeft((n) => n - 1);
      })();
    }, intervalSeconds * 1000);
    return () => clearTimeout(id);
    // `step` is intentionally omitted: it is redefined every render, and including
    // it would cancel and reschedule the timer on each one, so the interval would
    // never elapse.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRunning, stepsLeft, intervalSeconds]);

  if (!clock.enabled) return null;

  const running = clock.offset_days > 0;

  return (
    <section
      aria-labelledby="demo-clock-title"
      className={`w-full rounded-xl border p-5 sm:p-6 ${
        running
          ? "border-violet-300 bg-violet-50 dark:border-violet-500/30 dark:bg-[#171226]"
          : "border-line bg-panel"
      }`}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 id="demo-clock-title" className="text-sm font-semibold text-ink">
          Demo controls
        </h2>
        {running ? (
          <span className="rounded bg-violet-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-violet-800 ring-1 ring-inset ring-violet-300 dark:bg-violet-500/20 dark:text-violet-200 dark:ring-violet-500/40">
            +{clock.offset_days} days simulated
          </span>
        ) : (
          <span className="rounded bg-panel-2 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-ink-3">
            Real time
          </span>
        )}
      </div>

      <div className="mt-3.5 border-t border-line pt-3 text-[10px] font-semibold uppercase tracking-wider text-ink-4">
        Time machine
      </div>
      <p className="mt-1.5 text-xs leading-relaxed text-ink-3">
        Reminders go out at 3, 10 and 21 days overdue. Rather than wait three weeks,
        move the clock and watch the real recovery cycle react to the later date.
        Nothing is faked — the policy engine still decides what is due, under the same
        rules.
      </p>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-line bg-surface px-3 py-2">
          <div className="text-[10px] uppercase tracking-wider text-ink-4">
            System believes it is
          </div>
          <div className="mt-0.5 font-mono text-sm tabular-nums text-ink">
            {clock.simulated_date}
          </div>
        </div>
        <div className="rounded-lg border border-line bg-surface px-3 py-2">
          <div className="text-[10px] uppercase tracking-wider text-ink-4">
            Actual time now
          </div>
          <div className="mt-0.5 font-mono text-sm tabular-nums text-ink-3">
            {liveRealTime}
          </div>
        </div>
      </div>

      {/* ---- settings ---- */}
      <div className="mt-3.5 flex flex-col gap-2.5 border-t border-line pt-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="w-28 text-xs text-ink-3">Each step jumps</span>
          {STEP_CHOICES.map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded-md px-2.5 py-1 text-xs transition ${
                days === d
                  ? "bg-invert font-medium text-invert-ink"
                  : "text-ink-3 ring-1 ring-inset ring-line hover:text-ink"
              }`}
            >
              {d}d
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="w-28 text-xs text-ink-3">Auto-play every</span>
          {INTERVAL_CHOICES.map((sec) => (
            <button
              key={sec}
              onClick={() => setIntervalSeconds(sec)}
              className={`rounded-md px-2.5 py-1 text-xs transition ${
                intervalSeconds === sec
                  ? "bg-invert font-medium text-invert-ink"
                  : "text-ink-3 ring-1 ring-inset ring-line hover:text-ink"
              }`}
            >
              {sec}s
            </button>
          ))}
        </div>

        <label className="flex items-center gap-2 text-xs text-ink-3">
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
            className="h-3.5 w-3.5 accent-current"
          />
          Evaluate only — decide everything, send nothing
        </label>
      </div>

      {/* ---- actions ---- */}
      <div className="mt-3.5 flex flex-wrap items-center gap-2">
        <button
          onClick={() => void step()}
          disabled={busy}
          className="rounded-md bg-invert px-3 py-1.5 text-sm font-medium text-invert-ink transition hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Advancing…" : `Advance ${days} days`}
        </button>

        {autoRunning ? (
          <button
            onClick={() => setAuto(false)}
            className="rounded-md px-3 py-1.5 text-sm text-ink-2 ring-1 ring-inset ring-line transition hover:bg-panel-2 hover:text-ink"
          >
            Stop auto-play ({stepsLeft} left)
          </button>
        ) : (
          <button
            onClick={() => {
              // Fire the first step immediately so the panel reacts to the click,
              // then let the effect pace the rest.
              void step();
              setStepsLeft(MAX_AUTO_STEPS - 1);
              setAuto(true);
            }}
            disabled={busy}
            className="rounded-md px-3 py-1.5 text-sm text-ink-2 ring-1 ring-inset ring-line transition hover:bg-panel-2 hover:text-ink disabled:opacity-50"
          >
            Auto-play the whole cadence
          </button>
        )}

        <button
          onClick={() => void resetClock()}
          disabled={busy || !running}
          className="rounded-md px-3 py-1.5 text-sm text-ink-3 transition hover:text-ink disabled:opacity-40"
        >
          Back to real time
        </button>
      </div>

      {/* ---- where mail goes ---- */}
      <div className="mt-4 border-t border-line pt-3">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-4">
          Send reminders to
        </div>
        <p className="mt-1.5 text-xs leading-relaxed text-ink-3">
          Put your own address here to receive a real reminder and reply to it — that
          reply comes back through the live inbound path and can open a dispute.
          Customers are never emailed either way; this only moves the redirect.
        </p>

        <div className="mt-2 flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2">
          <span className="font-mono text-xs text-ink" title={clock.email_to ?? undefined}>
            {clock.email_to ?? "not configured"}
          </span>
          <span
            className={`ml-auto shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${
              clock.email_is_override
                ? "bg-violet-100 text-violet-800 dark:bg-violet-500/20 dark:text-violet-200"
                : "bg-panel-2 text-ink-3"
            }`}
          >
            {clock.email_is_override ? "your address" : "default"}
          </span>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            type="email"
            value={emailDraft}
            onChange={(e) => setEmailDraft(e.target.value)}
            placeholder="you@example.com"
            aria-label="Redirect reminder email to"
            className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink outline-none placeholder:text-ink-4 focus:border-ink-4"
          />
          <button
            onClick={() => void saveEmail(emailDraft)}
            disabled={emailSaving || !emailDraft.includes("@")}
            className="rounded-md bg-invert px-3 py-1.5 text-xs font-medium text-invert-ink transition hover:opacity-90 disabled:opacity-40"
          >
            {emailSaving ? "Saving…" : "Use this address"}
          </button>
          {clock.email_is_override ? (
            <button
              onClick={() => void saveEmail(null)}
              disabled={emailSaving}
              className="rounded-md px-2.5 py-1.5 text-xs text-ink-3 transition hover:text-ink disabled:opacity-40"
            >
              Reset
            </button>
          ) : null}
        </div>

        {emailNote ? (
          <p className="mt-1.5 text-xs text-emerald-700 dark:text-emerald-400">{emailNote}</p>
        ) : null}
      </div>

      {report ? (
        <p className="mt-3 rounded-lg border border-line bg-surface px-3 py-2 text-xs text-ink-2">
          Last step{dryRun ? " (evaluated, nothing sent)" : ""}:{" "}
          <strong className="text-ink">{report.considered}</strong> invoices considered ·{" "}
          <strong className="text-ink">{report.sent}</strong> {dryRun ? "would send" : "sent"} ·{" "}
          <strong className="text-ink">{report.held}</strong> held ·{" "}
          <strong className="text-ink">{report.escalated}</strong> escalated
          {report.promises_broken ? ` · ${report.promises_broken} promise(s) broken` : ""}
        </p>
      ) : null}

      {error ? (
        <p className="mt-2.5 text-xs text-rose-700 dark:text-rose-300">{error}</p>
      ) : null}
    </section>
  );
}
