"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

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
  const router = useRouter();
  const [merchant, setMerchant] = useState("");
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [subscription, setSubscription] = useState<SubscriptionState | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // The password is collected by a real form, not `window.prompt`. The browser dialog
  // could not be styled, showed the bare origin as if it were a system prompt, and is
  // exactly the shape a phishing page imitates — a poor place to ask for a password
  // immediately before taking money.
  const [pendingTrial, setPendingTrial] = useState<boolean | null>(null);
  const [password, setPassword] = useState("");
  // Set while the merchant is away on Razorpay's hosted page.
  const [awaitingPayment, setAwaitingPayment] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  // Refs, not state, because both guards have to hold BEFORE the next render.
  //
  // `busy` only disables the button once React has re-rendered. A merchant holding
  // Enter — or clicking twice — submits again inside that gap, and every one of those
  // submissions used to run in full: four Enters produced four re-auth calls, four
  // checkout requests and four Razorpay tabs. The extra requests are what filled the
  // console with errors, and two checkout calls racing each other can get past the
  // server's "you already have a checkout in flight" test and register a second
  // subscription on the merchant's Razorpay account.
  const submitting = useRef(false);
  // The activation poller, so it can be stopped from anywhere — including unmount.
  // It used to be a local `timer`, which meant nothing could cancel it once the page
  // went away: leaving mid-payment left an interval calling the API every three
  // seconds for the rest of the session, against a component that no longer existed.
  const poll = useRef<number | null>(null);

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

  const stopPolling = useCallback(() => {
    if (poll.current !== null) {
      window.clearInterval(poll.current);
      poll.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  /**
   * Re-read the subscription after the merchant comes back from checkout.
   *
   * Coming back means one of two things and the page cannot tell them apart on its
   * own: the payment was abandoned, or it went through and the webhook landed in the
   * moment between the last poll and the tab closing. Asking the server settles it,
   * so a merchant who did pay is not left staring at the plan picker.
   */
  const refreshSubscription = useCallback(async () => {
    if (!merchant) return;
    try {
      const state = await liveGet<SubscriptionState>("/api/live/billing/subscription", merchant);
      setSubscription(state);
      if (state.is_active) {
        window.localStorage.removeItem("vasooli_pending_plan");
        router.replace("/live");
      }
    } catch {
      /* The plan choice on screen is still valid; leave it alone. */
    }
  }, [merchant, router]);

  // Whether the free trial is still on the table. The server is the authority; this
  // only decides which buttons to render, and the response corrects the page if the
  // two ever disagree.
  const trialAvailable = subscription?.mandate_verification_paise != null;
  const mandatePaise = subscription?.mandate_verification_paise ?? 200;
  const plan = plans.find((item) => item.slug === selected) ?? null;

  /**
   * Wait for the subscription to go live, then finish the journey.
   *
   * Razorpay's hosted page is opened in its own tab, so this tab stays exactly where
   * it was. Two things follow from that, and both are what a merchant expects:
   * a completed payment lands them in the workspace and the payment tab is closed for
   * them, and an abandoned one leaves them on this page with their plan still chosen
   * — no dead end, nothing to re-enter.
   *
   * Polling rather than a provider redirect: the checkout tab is on Razorpay's origin,
   * so it cannot talk to this one, and the subscription only becomes active when the
   * webhook arrives — which can land after the redirect either way.
   */
  function waitForActivation(checkoutTab: Window | null) {
    // Only ever one watcher. Anything already running is watching a tab the merchant
    // has moved on from, and letting them accumulate meant a stale one could fire
    // "Checkout timed out" over a checkout that was going perfectly well.
    stopPolling();
    // Computed on the first tick rather than here: `Date.now()` in the function body
    // is read during render, which the purity rule correctly rejects.
    let deadline = 0;
    poll.current = window.setInterval(async () => {
      if (deadline === 0) deadline = Date.now() + 15 * 60 * 1000;
      // Merchant closed the payment tab themselves: stop waiting, leave them here —
      // but ask the server first, in case they closed it on a payment that worked.
      if (checkoutTab && checkoutTab.closed) {
        stopPolling();
        setAwaitingPayment(false);
        void refreshSubscription();
        return;
      }
      // Fifteen minutes is longer than any hosted page stays useful.
      if (Date.now() > deadline) {
        stopPolling();
        setAwaitingPayment(false);
        setError("Checkout timed out. If you completed the payment, reload this page.");
        return;
      }
      try {
        const state = await liveGet<SubscriptionState>(
          "/api/live/billing/subscription",
          merchant,
        );
        if (!state.is_active) return;
        stopPolling();
        window.localStorage.removeItem("vasooli_pending_plan");
        // Close the tab this page opened, then move to the workspace.
        try {
          checkoutTab?.close();
        } catch {
          /* Blocked by the browser: the merchant closes it, which is harmless. */
        }
        router.replace("/live");
      } catch {
        /* A transient failure here is not a payment failure; keep waiting. */
      }
    }, 3000);
  }

  async function pay(startTrial: boolean, confirmPassword: string) {
    if (!plan || !merchant) return;
    // The guard that actually holds. Set synchronously, so the second and third
    // Enter of a held key return here instead of starting another checkout.
    if (submitting.current) return;
    submitting.current = true;
    setBusy(plan.slug);
    setError(null);
    try {
      // Checkout is a sensitive action and requires a fresh re-auth proof, exactly as
      // the billing page does. Without it the request is refused with 428.
      const challenge = await reauthLive(confirmPassword);
      const result = await livePost<CheckoutResult>(
        "/api/live/billing/checkout",
        merchant,
        { plan_slug: plan.slug, start_trial: startTrial },
        { "X-Reauth-Token": challenge.reauth_token },
      );
      if (result.checkout_url) {
        const tab = window.open(result.checkout_url, "_blank", "noopener=false");
        if (!tab) {
          // Nothing opened, so nothing has been confirmed: keep the merchant on the
          // form they were already filling in rather than clearing it and sending
          // them back to pick a plan again to read a pop-up warning.
          setError(
            "Your browser blocked the payment window. Allow pop-ups for this site and try again.",
          );
          return;
        }
        // Cleared only now that the tab is genuinely open. The password does not stay
        // in component state a moment longer than the request that needed it.
        setPassword("");
        setPendingTrial(null);
        setAwaitingPayment(true);
        waitForActivation(tab);
        return;
      }
      setError(
        "Your plan is reserved, but online payment is not configured on this deployment yet. Contact support to complete it.",
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start checkout");
    } finally {
      // Released on every path, including the failed ones: a mistyped password has to
      // stay retryable. What it prevents is a second attempt while the first is still
      // in flight, not a second attempt at all.
      submitting.current = false;
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

      {plan && awaitingPayment ? (
        <div className="flex flex-col gap-2 rounded-lg border border-line bg-panel px-4 py-4">
          <p className="text-sm font-medium text-ink">Waiting for your payment…</p>
          <p className="text-sm leading-6 text-ink-2">
            Finish the payment in the tab that just opened. This page will take you into your
            workspace as soon as it clears, and close that tab for you.
          </p>
          <p className="text-xs leading-5 text-ink-3">
            Changed your mind? Close the payment tab and you will come straight back here with
            your plan still selected. Nothing is charged.
          </p>
        </div>
      ) : null}

      {plan && !awaitingPayment && pendingTrial !== null ? (
        <form
          className="flex flex-col gap-3 rounded-lg border border-line bg-panel px-4 py-4"
          aria-busy={busy !== null}
          onSubmit={(event) => {
            event.preventDefault();
            // Repeated here as well as inside `pay` so the intent is visible at the
            // point a browser can fire this several times over: implicit submission
            // from a held Enter key does not wait for React to disable the button.
            if (busy !== null || submitting.current) return;
            void pay(pendingTrial, password);
          }}
        >
          <p className="text-sm font-medium text-ink">
            {pendingTrial
              ? `Confirm to start your ${TRIAL_DAYS}-day trial`
              : `Confirm to start ${plan.name} now`}
          </p>
          <p className="text-sm leading-6 text-ink-2">
            {pendingTrial
              ? `${inr(mandatePaise)} is taken now and refunded automatically.`
              : `${inr(plan.amount_paise)} plus applicable taxes is authorised today.`}
          </p>
          <label className="flex flex-col gap-1.5 text-sm text-ink-2">
            Your password
            <input
              type="password"
              autoComplete="current-password"
              required
              // Read-only rather than disabled while the request is in flight: a
              // disabled field loses focus and empties itself in some password
              // managers, and the merchant should be able to see what they typed if
              // the attempt comes back rejected.
              readOnly={busy !== null}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent read-only:opacity-60"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              disabled={busy !== null}
              className="rounded-md bg-invert px-4 py-2 text-sm font-medium text-invert-ink transition hover:opacity-90 disabled:opacity-50"
            >
              {busy ? "Opening checkout…" : "Continue to payment"}
            </button>
            <button
              type="button"
              onClick={() => {
                setPendingTrial(null);
                setPassword("");
                setError(null);
              }}
              className="rounded-md px-4 py-2 text-sm text-ink-2 ring-1 ring-inset ring-line transition hover:bg-panel-2 hover:text-ink"
            >
              Back
            </button>
          </div>
        </form>
      ) : null}

      {plan && !awaitingPayment && pendingTrial === null ? (
        <div className="flex flex-col gap-3">
          {trialAvailable ? (
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => setPendingTrial(true)}
              className="w-full rounded-md bg-invert px-4 py-2.5 text-sm font-medium text-invert-ink transition hover:opacity-90 disabled:opacity-50"
            >
              {`Start ${TRIAL_DAYS}-day trial — pay ${inr(mandatePaise)} now`}
            </button>
          ) : null}
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => setPendingTrial(false)}
            className={`w-full rounded-md px-4 py-2.5 text-sm font-medium transition disabled:opacity-50 ${
              trialAvailable
                ? "text-ink-2 ring-1 ring-inset ring-line hover:bg-panel-2 hover:text-ink"
                : "bg-invert text-invert-ink hover:opacity-90"
            }`}
          >
            {`Start now — pay ${inr(plan.amount_paise)} + applicable taxes`}
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
