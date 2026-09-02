"use client";

import { FormEvent, useEffect, useState } from "react";

import { LiveSignInPrompt } from "@/components/LiveSignInPrompt";
import {
  SettingsAlert,
  SettingsCard,
  SettingsSectionHeader,
  fieldClass,
  labelClass,
  primaryButtonClass,
  secondaryButtonClass,
} from "@/components/LiveSettingsSection";
import { liveGet, livePost, reauthLive } from "@/lib/live-api";
import { PlanSummary, SubscriptionState, formatInr, useSubscription } from "@/lib/subscription";

type CheckoutResult = { checkout_url: string | null; plan: string; status: string };

/** Matches LIVE_TRIAL_DAYS on the server; the signup plan step promises the same. */
const TRIAL_DAYS = 7;

/** Reads as a sentence in the UI, so the singular case has to be right. */
function days(n: number) {
  return `${n} ${n === 1 ? "day" : "days"}`;
}

function statusLabel(s: SubscriptionState) {
  if (s.on_trial) return "Free trial";
  return { active: "Active", authenticated: "Active", past_due: "Payment failed", paused: "Paused", cancelled: "Cancelled", expired: "Expired", trial_expired: "Trial ended", created: "Awaiting payment" }[s.status] ?? s.status;
}

export default function LiveBillingPage() {
  const [merchant, setMerchant] = useState("");
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [sentHere, setSentHere] = useState<"gate" | "signup" | null>(null);

  useEffect(() => {
    Promise.resolve().then(() => {
      setMerchant(window.localStorage.getItem("vasooli_live_merchant") || "");
      // PaymentGate redirects here on a 402. Saying so beats dropping someone on a
      // pricing page with no explanation of why their workspace closed.
      const reason = new URLSearchParams(window.location.search).get("reason");
      setSentHere(reason === "payment_required" ? "gate" : reason === "new_signup" ? "signup" : null);
      // Carried from the signup plan step. Consumed on read so a later visit does
      // not silently re-select a plan the merchant has since changed their mind about.
      const pending = window.localStorage.getItem("vasooli_pending_plan");
      if (pending) {
        setSelected(pending);
        window.localStorage.removeItem("vasooli_pending_plan");
      }
    });
  }, []);

  const { subscription, loaded, refresh } = useSubscription(merchant);

  useEffect(() => {
    if (!merchant) return;
    liveGet<PlanSummary[]>("/api/live/billing/plans", merchant)
      .then(setPlans)
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Unable to load plans"));
  }, [merchant]);

  async function startCheckout(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || !merchant) return;
    const password = String(new FormData(event.currentTarget).get("password"));
    setBusy(selected);
    setError(null);
    setMessage(null);
    try {
      const proof = await reauthLive(password);
      const result = await livePost<CheckoutResult>(
        "/api/live/billing/checkout",
        merchant,
        { plan_slug: selected },
        { "X-Reauth-Token": proof.reauth_token },
      );
      if (result.checkout_url) {
        // Razorpay's hosted page is where the mandate is authorised and money moves.
        window.location.assign(result.checkout_url);
        return;
      }
      setMessage(
        "Your plan is reserved, but online payment is not configured on this deployment yet. Contact support to complete it.",
      );
      setSelected(null);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Checkout could not be started");
    } finally {
      setBusy(null);
    }
  }

  async function cancelSubscription(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const password = String(new FormData(event.currentTarget).get("password"));
    setBusy("cancel");
    setError(null);
    try {
      const proof = await reauthLive(password);
      await livePost("/api/live/billing/cancel", merchant, undefined, {
        "X-Reauth-Token": proof.reauth_token,
      });
      setConfirmCancel(false);
      setMessage("Your subscription will not renew. You keep full access until the period ends.");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Cancellation failed");
    } finally {
      setBusy(null);
    }
  }

  if (!merchant) return <LiveSignInPrompt what="Billing and plans" />;

  return (
    <div className="space-y-6">
      <SettingsSectionHeader
        eyebrow="Account"
        title="Billing"
        description="Your plan, what it includes, and when it renews. Prices exclude applicable taxes."
      />

      {sentHere !== null ? (
        <div
          role="status"
          className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm leading-6 text-amber-900 dark:text-amber-200"
        >
          <p className="font-semibold">
            {sentHere === "signup"
              ? "One step left: confirm your plan."
              : "Choose a plan to open your workspace."}
          </p>
          <p className="mt-1">
            {sentHere === "signup"
              ? `Your ${TRIAL_DAYS}-day free trial starts as soon as you confirm. A ₹2 charge verifies your Autopay mandate and is refunded automatically.`
              : "Your subscription is not active yet, so the workspace is closed. Your data is safe and nothing has been deleted — pick a plan below and it opens again."}
          </p>
        </div>
      ) : null}

      {subscription ? (
        <section className="rounded-xl border border-line bg-panel p-5">
          <div className="flex flex-wrap items-start gap-x-4 gap-y-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-lg font-semibold text-ink">{subscription.plan.name}</h3>
                <span
                  className={`rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider ${
                    subscription.is_active
                      ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                      : "bg-rose-500/10 text-rose-700 dark:text-rose-300"
                  }`}
                >
                  {statusLabel(subscription)}
                </span>
              </div>
              <p className="mt-1 text-sm text-ink-3">
                {formatInr(subscription.plan.amount_paise)} / month · {subscription.plan.included_active_invoices.toLocaleString("en-IN")} active invoices ·{" "}
                {subscription.plan.included_seats === 1
                  ? "1 user"
                  : `up to ${subscription.plan.included_seats} users`}
              </p>
            </div>

            <div className="ml-auto text-right">
              <p className="text-2xl font-semibold tabular-nums text-ink">
                {subscription.days_remaining}
              </p>
              <p className="text-xs text-ink-4">
                {subscription.on_trial ? "days left in trial" : subscription.is_active ? "days until renewal" : "days remaining"}
              </p>
            </div>
          </div>

          {subscription.cancel_at_period_end ? (
            <p className="mt-4 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
              This subscription will not renew. You keep {days(subscription.days_remaining)} of full access.
            </p>
          ) : null}

          {subscription.paused_reason ? (
            <p className="mt-4 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-800 dark:text-rose-200">
              {subscription.paused_reason} Your data stays available to view and export — only automation is paused.
            </p>
          ) : null}

          {subscription.is_active && !subscription.on_trial && !subscription.cancel_at_period_end ? (
            <div className="mt-4">
              {confirmCancel ? (
                <form onSubmit={cancelSubscription} className="flex flex-wrap items-end gap-3 rounded-lg border border-line bg-surface p-4">
                  <label className={`min-w-56 flex-1 ${labelClass}`}>
                    Confirm current password
                    <input name="password" type="password" required autoComplete="current-password" className={fieldClass} />
                  </label>
                  <button disabled={busy !== null} className={primaryButtonClass}>
                    {busy === "cancel" ? "Cancelling…" : "Confirm cancellation"}
                  </button>
                  <button type="button" onClick={() => setConfirmCancel(false)} className={secondaryButtonClass}>
                    Keep my plan
                  </button>
                </form>
              ) : (
                <button type="button" onClick={() => setConfirmCancel(true)} className="text-xs font-medium text-ink-3 underline underline-offset-4 hover:text-ink">
                  Cancel subscription
                </button>
              )}
            </div>
          ) : null}
        </section>
      ) : loaded ? null : (
        <p className="text-sm text-ink-4">Loading your plan…</p>
      )}

      <section className="space-y-4">
        <SettingsSectionHeader
          eyebrow="Plans"
          title={subscription?.is_active && !subscription.on_trial ? "Change plan" : "Choose a plan"}
          description="Billed monthly and cancellable at any time. An active invoice is one currently in an open recovery workflow."
        />

        <div className="grid gap-4 lg:grid-cols-3">
          {plans.map((plan) => {
            const current = subscription?.plan.slug === plan.slug && !subscription.on_trial;
            const picked = selected === plan.slug;
            return (
              <article
                key={plan.slug}
                className={`flex flex-col rounded-xl border bg-panel p-5 transition ${picked ? "border-accent ring-1 ring-accent/30" : "border-line"}`}
              >
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-semibold text-ink">{plan.name}</h3>
                  {current ? (
                    <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-accent">
                      Current
                    </span>
                  ) : null}
                </div>
                {plan.description ? <p className="mt-1 text-xs leading-5 text-ink-4">{plan.description}</p> : null}
                <p className="mt-3 text-2xl font-semibold tabular-nums text-ink">
                  {formatInr(plan.amount_paise)}
                  <span className="text-sm font-normal text-ink-4"> / month</span>
                </p>
                <p className="mt-2 text-xs text-ink-3">
                  {plan.included_active_invoices.toLocaleString("en-IN")} active invoices ·{" "}
                  {plan.included_seats === 1 ? "1 user" : `up to ${plan.included_seats} users`}
                </p>
                <ul className="mt-3 space-y-1.5 text-xs leading-5 text-ink-3">
                  {(plan.highlights ?? []).map((h) => (
                    <li key={h} className="flex gap-2">
                      <span aria-hidden className="text-accent">✓</span>
                      {h}
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  disabled={busy !== null || current}
                  onClick={() => setSelected(picked ? null : plan.slug)}
                  aria-pressed={picked}
                  className={`mt-5 w-full ${picked ? primaryButtonClass : secondaryButtonClass} disabled:opacity-40`}
                >
                  {current ? "Your current plan" : picked ? "Selected" : `Choose ${plan.name}`}
                </button>
              </article>
            );
          })}
        </div>
      </section>

      {selected ? (
        <SettingsCard
          title={`Continue with ${plans.find((p) => p.slug === selected)?.name ?? selected}`}
          hint="Re-entering your password authorises the subscription. You will be taken to Razorpay to complete it."
        >
          {subscription?.on_trial ? (
            <div className="mb-4 rounded-lg border border-line bg-surface px-3 py-2.5 text-xs leading-5 text-ink-3">
              <p className="font-semibold text-ink">A ₹2 charge confirms your Autopay mandate.</p>
              <p className="mt-1">
                Your bank or UPI app needs a real payment to confirm you approved recurring
                debits, so ₹2 is taken now and <strong className="font-semibold text-ink">refunded
                automatically</strong> once the mandate is confirmed. Your plan itself is not
                charged until the trial ends, and you can cancel at any time.
              </p>
            </div>
          ) : null}
          <form onSubmit={startCheckout} className="flex flex-wrap items-end gap-3">
            <label className={`min-w-56 flex-1 ${labelClass}`}>
              Confirm current password
              <input name="password" type="password" required autoComplete="current-password" className={fieldClass} />
            </label>
            <button disabled={busy !== null} className={primaryButtonClass}>
              {busy === selected ? "Starting checkout…" : "Continue to payment"}
            </button>
            <button type="button" onClick={() => setSelected(null)} className={secondaryButtonClass}>
              Cancel
            </button>
          </form>
        </SettingsCard>
      ) : null}

      {message ? <SettingsAlert tone="success">{message}</SettingsAlert> : null}
      {error ? <SettingsAlert tone="error">{error}</SettingsAlert> : null}
    </div>
  );
}
