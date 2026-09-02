"use client";

import { useCallback, useEffect, useState } from "react";

import { liveGet } from "@/lib/live-api";

export type PlanSummary = {
  slug: string;
  name: string;
  amount_paise: number;
  included_active_invoices: number;
  included_seats: number;
  features: string[];
  description?: string;
  highlights?: string[];
};

export type SubscriptionState = {
  status: string;
  plan: PlanSummary;
  is_active: boolean;
  on_trial: boolean;
  days_remaining: number;
  period_end: string | null;
  cancel_at_period_end: boolean;
  /** Set only when automation is stopped. Reads and exports stay available. */
  paused_reason: string | null;
  /** Set while service continues but needs attention, e.g. a card failure in grace. */
  warning: string | null;
  provider_subscription_id: string | null;
  checkout_url?: string | null;
  id?: string;
};

/**
 * The workspace's billing state, fetched once per mount.
 *
 * Deliberately fails open: if this request errors the UI must not decide the
 * merchant is unpaid and start hiding their own ledger from them. The server
 * refuses billable writes on its own, so a wrong answer here can only ever be
 * cosmetic — never the thing standing between someone and their data.
 */
export function useSubscription(merchant: string) {
  const [subscription, setSubscription] = useState<SubscriptionState | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!merchant) return;
    let live = true;
    // setState only from the promise callback: calling it synchronously in the
    // effect body cascades a render, which the lint rule here exists to prevent.
    liveGet<SubscriptionState>("/api/live/billing/subscription", merchant)
      .then((value) => {
        if (live) setSubscription(value);
      })
      .catch(() => {
        if (live) setSubscription(null);
      })
      .finally(() => {
        if (live) setLoaded(true);
      });
    return () => {
      live = false;
    };
  }, [merchant, nonce]);

  /** Re-read after checkout or cancellation. Safe to call from an event handler. */
  const refresh = useCallback(async () => {
    setNonce((n) => n + 1);
  }, []);

  return { subscription, loaded, refresh };
}


export function formatInr(paise: number) {
  return `₹${(paise / 100).toLocaleString("en-IN")}`;
}
