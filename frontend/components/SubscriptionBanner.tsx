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
    // Headline, detail and action were one wrapping flex row. On a narrow column the
    // detail sentence pushed the button onto its own line hard against the text, and
    // on a wide one the headline and a long sentence ran together as if they were one
    // paragraph. Stacking the text and holding the action beside it keeps both
    // readable at any width, and the button no longer collides with the copy.
    <div role="status" className={`border-b px-4 py-4 sm:px-6 sm:py-5 lg:px-8 ${tone}`}>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
        <div className="flex min-w-0 flex-col gap-1.5">
          <p className="text-sm font-semibold leading-5">{headline}</p>
          <p className="max-w-3xl text-sm leading-6 opacity-90">{detail}</p>
        </div>
        <Link
          href="/live/settings/billing"
          className="shrink-0 self-start rounded-lg bg-invert px-3.5 py-2 text-xs font-semibold text-invert-ink transition hover:opacity-90 sm:mt-0.5"
        >
          {paused ? "Resume automation" : "Manage billing"}
        </Link>
      </div>
    </div>
  );
}
