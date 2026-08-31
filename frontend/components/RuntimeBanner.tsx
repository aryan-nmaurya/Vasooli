"use client";

import { useEffect, useState } from "react";

import { getRuntimeSafety } from "@/lib/api";

export function RuntimeBanner() {
  const [mode, setMode] = useState<Awaited<ReturnType<typeof getRuntimeSafety>> | null>(null);

  useEffect(() => { getRuntimeSafety().then(setMode).catch(() => setMode(null)); }, []);
  if (!mode) return null;

  const email =
    mode.email === "dry_run"
      ? "Email dry-run"
      : mode.email === "redirected"
        ? "Email redirected to operator"
        : "Direct customer email";

  return (
    <aside
      aria-label="Runtime safety modes"
      className={`border-b px-3 py-1.5 text-center text-[10px] font-medium tracking-wide sm:px-6 ${
        mode.email === "direct_customer" || mode.razorpay === "live"
          ? "border-rose-300 bg-rose-50 text-rose-900 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
          : "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
      }`}
    >
      {email} · Razorpay {mode.razorpay} mode · Scheduler {mode.scheduler} · Inbound {mode.inbound_email.replaceAll("_", " ")} · AI {mode.ai.replaceAll("_", " ")}
    </aside>
  );
}
