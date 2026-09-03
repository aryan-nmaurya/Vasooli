"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { liveGet, livePost, reauthLive } from "@/lib/live-api";
import type { PlanSummary, SubscriptionState } from "@/lib/subscription";

/**
 * The first payment, on its own page, outside the dashboard.
 *
 * Signup used to hand a brand-new merchant straight to `/live/settings/billing`. That
 * screen exists to manage an established subscription — it shows renewal dates, a
 * cancel control and a plan-change grid — and none of that is true yet for someone who
 * has never paid. Worse, it made the ₹2 mandate charge look like a permanent feature of
 * billing rather than what it is: a one-time step on the way in.
 *
 * So the two are separated. This page does one thing, once, and states the exact figure
 * before anything is authorised. `/live/settings/billing` no longer mentions the
 * mandate at all.
 *
 * The two choices take genuinely different money, which is why they are two buttons and
 * not a checkbox:
 *
 *   Trial      — ₹2 now, refunded once Razorpay confirms the mandate. The plan amount
 *                is not charged until the trial ends.
 *   Start now  — the full plan amount today, no trial, no mandate fee.
 *
 * `start_trial` is a request, not an instruction: the server still decides via
 * `trial_is_available`, and the response reports what actually happened. A returning
 * merchant who asks for a trial gets a normal paid checkout, and this page reads the
 * server's answer rather than repeating its own assumption.
 */

const TRIAL_DAYS = 7;

type CheckoutResult = {
  checkout_url: string | null;
  plan: string;
  trial_applied: boolean;
  amount_due_now_paise: number | null;
  amount_paise: number | null;
};

function inr(paise: number | null | undefined) {
  if (paise == null) return "—";
  return `₹${(paise / 100).toLocaleString("en-IN")}`;
}

export default function StartPage() {
  const [merchant, setMerchant] = useState("");
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [subscription, setSubscription] = useState<SubscriptionState | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    const id = window.localStorage.getItem("vasooli_live_merchant") ?? "";
    // The plan chosen during signup rides here in storage so this page opens on it.
    const pending = window.localStorage.getItem("vasooli_pending_plan");
    // Every setState below happens in a callback, never synchronously in the effect
    // body: doing it inline cascades an extra render, which is what the
    // `set-state-in-effect` rule exists to prevent. `useSubscription` avoids it the
    // same way.
    const load = id
      ? Promise.all([
          liveGet<PlanSummary[]>("/api/live/billing/plans", id),
          liveGet<SubscriptionState>("/api/live/billing/subscription", id).catch(() => null),
        ])
      : Promise.resolve<[PlanSummary[], SubscriptionState | null]>([[], null]);
    load
      .then(([catalog, state]) => {
        if (!alive) return;
        setMerchant(id);
        setSelected(pending);
        setPlans(catalog);
        setSubscription(state);
      })
      .catch((cause) => {
        if (alive) setError(cause instanceof Error ? cause.message : "Could not load plans");
      })
      .finally(() => {
        if (alive) setLoaded(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  // Whether the free trial is still on the table. The server is the authority; this
  // only decides which buttons to render, and the response corrects the page if the
  // two ever disagree.
  const trialAvailable = subscription?.mandate_verification_paise != null;
  const mandatePaise = subscription?.mandate_verification_paise ?? 200;
  const plan = plans.find((item) => item.slug === selected) ?? null;

  async function pay(startTrial: boolean) {
    if (!plan || !merchant) return;
    setBusy(plan.slug);
    setError(null);
    try {
      // Checkout is a sensitive action and requires a fresh re-auth proof, exactly as
      // the billing page does. Without it the request is refused with 428.
      const challenge = await reauthLive(
        window.prompt("Confirm your password to authorise this payment") ?? "",
      );
      const result = await livePost<CheckoutResult>(
        "/api/live/billing/checkout",
        merchant,
        { plan_slug: plan.slug, start_trial: startTrial },
        { "X-Reauth-Token": challenge.reauth_token },
      );
      if (result.checkout_url) {
        window.localStorage.removeItem("vasooli_pending_plan");
        // Razorpay's hosted page is where the mandate is authorised and money moves.
        window.location.assign(result.checkout_url);
        return;
      }
      setError(
        "Your plan is reserved, but online payment is not configured on this deployment yet. Contact support to complete it.",
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start checkout");
    } finally {
      setBusy(null);
    }
  }

  if (!loaded) {
    return <p className="mx-auto max-w-xl px-4 py-16 text-sm text-ink-3">Loading your plan…</p>;
  }

  if (!merchant) {
    return (
      <div className="mx-auto max-w-xl px-4 py-16 text-center">
        <h1 className="text-2xl font-semibold text-ink">Sign in to continue.</h1>
        <p className="mt-2 text-[15px] text-ink-2">
          Your workspace opens once payment is confirmed.
        </p>
        <Link
          href="/live/login"
          className="mt-4 inline-block rounded-md bg-invert px-3 py-1.5 text-sm font-medium text-invert-ink"
        >
          Go to sign in
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-6 px-4 py-12">
      <header className="flex flex-col gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-4">
          Final step
        </span>
        <h1 className="text-2xl font-semibold tracking-tight text-ink">
          Activate your workspace.
        </h1>
        <p className="text-[15px] leading-relaxed text-ink-2">
          Choose how you want to start. Both options open the full workspace immediately.
        </p>
      </header>

      <div className="flex flex-col gap-2">
        <span className="text-xs font-medium uppercase tracking-wider text-ink-3">Your plan</span>
        <div className="grid gap-2">
          {plans.map((item) => {
            const picked = selected === item.slug;
            return (
              <button
                key={item.slug}
                type="button"
                aria-pressed={picked}
                onClick={() => setSelected(item.slug)}
                className={`flex w-full items-baseline gap-2 rounded-lg border px-4 py-3 text-left transition ${
                  picked ? "border-accent bg-panel-2" : "border-line hover:border-line-2"
                }`}
              >
                <span className="text-sm font-semibold text-ink">{item.name}</span>
                <span className="ml-auto text-sm font-semibold tabular-nums text-ink">
                  {inr(item.amount_paise)}
                  <span className="font-normal text-ink-3"> / month</span>
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {error ? (
        <p role="alert" className="rounded-lg border border-line bg-panel px-4 py-3 text-sm text-ink">
          {error}
        </p>
      ) : null}

      {plan ? (
        <div className="flex flex-col gap-3">
          {trialAvailable ? (
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => pay(true)}
              className="w-full rounded-md bg-invert px-4 py-2.5 text-sm font-medium text-invert-ink transition hover:opacity-90 disabled:opacity-50"
            >
              {busy ? "Starting checkout…" : `Start ${TRIAL_DAYS}-day trial — pay ${inr(mandatePaise)} now`}
            </button>
          ) : null}
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => pay(false)}
            className={`w-full rounded-md px-4 py-2.5 text-sm font-medium transition disabled:opacity-50 ${
              trialAvailable
                ? "text-ink-2 ring-1 ring-inset ring-line hover:bg-panel-2 hover:text-ink"
                : "bg-invert text-invert-ink hover:opacity-90"
            }`}
          >
            {busy
              ? "Starting checkout…"
              : `Start now — pay ${inr(plan.amount_paise)} + applicable taxes`}
          </button>

          <p className="text-xs leading-relaxed text-ink-3">
            {trialAvailable ? (
              <>
                The {inr(mandatePaise)} charge is how your bank or UPI app confirms an Autopay
                mandate — a mandate cannot be approved for nothing. It is refunded automatically
                once the mandate is confirmed, and {inr(plan.amount_paise)} is not charged until
                your {TRIAL_DAYS}-day trial ends. Cancel any time before then and you pay nothing.
              </>
            ) : (
              <>
                {inr(plan.amount_paise)} plus applicable taxes is authorised today and billed
                monthly. You can cancel at any time from billing settings.
              </>
            )}
          </p>
        </div>
      ) : (
        <p className="text-sm text-ink-3">Pick a plan to continue.</p>
      )}
    </div>
  );
}
