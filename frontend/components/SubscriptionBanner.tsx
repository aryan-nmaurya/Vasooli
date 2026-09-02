"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useSubscription } from "@/lib/subscription";

/**
 * The persistent state-of-billing strip across the live workspace.
 *
 * Says what stopped and what still works, because the failure mode this replaces is
 * a dashboard that simply looks broken. Vasooli holds a merchant's receivables and
 * audit trail; when automation pauses they must be told their data is still theirs,
 * in the same breath as being asked to pay.
 */
export function SubscriptionBanner() {
  const [merchant, setMerchant] = useState("");
  useEffect(() => {
    Promise.resolve().then(() =>
      setMerchant(window.localStorage.getItem("vasooli_live_merchant") || ""),
    );
  }, []);
  const { subscription } = useSubscription(merchant);

  if (!subscription) return null;

  const paused = subscription.paused_reason;
  const warning = subscription.warning;
  const trialEnding = subscription.on_trial && subscription.days_remaining <= 3;

  if (!paused && !warning && !trialEnding) return null;

  const tone = paused
    ? "border-rose-500/30 bg-rose-500/10 text-rose-900 dark:text-rose-200"
    : "border-amber-500/30 bg-amber-500/10 text-amber-900 dark:text-amber-200";

  const headline = paused
    ? "Automation paused"
    : warning
      ? "Payment needs attention"
      : `Trial ends in ${subscription.days_remaining} ${subscription.days_remaining === 1 ? "day" : "days"}`;

  const detail = paused
    ? `${paused} Your data remains available — you can still view and export everything.`
    : warning
      ? warning
      : "Choose a plan to keep invoice sync, recovery automation and customer reminders running.";

  return (
    <div role="status" className={`border-b px-4 py-3 sm:px-6 lg:px-8 ${tone}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <p className="text-sm font-semibold">{headline}</p>
        <p className="min-w-0 text-sm leading-5 opacity-90">{detail}</p>
        <Link
          href="/live/settings/billing"
          className="ml-auto shrink-0 rounded-lg bg-invert px-3 py-1.5 text-xs font-semibold text-invert-ink transition hover:opacity-90"
        >
          {paused ? "Resume automation" : "Manage billing"}
        </Link>
      </div>
    </div>
  );
}
