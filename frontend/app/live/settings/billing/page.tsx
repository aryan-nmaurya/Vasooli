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

type Plan = {
  slug: string;
  name: string;
  amount_paise: number;
  included_active_invoices: number;
  included_seats: number;
};
type Subscription = { status: string; plan_id: string; provider_subscription_id: string | null } | null;

export default function LiveBillingPage() {
  const [merchant, setMerchant] = useState("");
  const [plans, setPlans] = useState<Plan[]>([]);
  const [subscription, setSubscription] = useState<Subscription>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [selectedPlan, setSelectedPlan] = useState<string | null>(null);

  useEffect(() => {
    const value = window.localStorage.getItem("vasooli_live_merchant") || "";
    Promise.resolve().then(() => setMerchant(value));
    if (!value) return;
    Promise.all([
      liveGet<Plan[]>("/api/live/billing/plans", value),
      liveGet<Subscription>("/api/live/billing/subscription", value),
    ])
      .then(([loadedPlans, loadedSubscription]) => {
        setPlans(loadedPlans);
        setSubscription(loadedSubscription);
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Unable to load billing"));
  }, []);

  async function choosePlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const slug = selectedPlan;
    if (!slug) return;
    if (!merchant) {
      setError("Sign in to a live workspace first.");
      return;
    }
    const password = String(new FormData(event.currentTarget).get("password"));
    setBusy(slug);
    setError(null);
    try {
      const proof = await reauthLive(password);
      const result = await livePost<{ status: string; plan: string }>(
        "/api/live/billing/checkout",
        merchant,
        { plan_slug: slug },
        { "X-Reauth-Token": proof.reauth_token },
      );
      setSubscription((current) => ({
        ...(current || { plan_id: "", provider_subscription_id: null }),
        status: result.status,
        plan_id: result.plan,
        provider_subscription_id: null,
      }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Checkout could not be started");
    } finally {
      setBusy(null);
    }
  }

  if (!merchant) return <LiveSignInPrompt what="Billing and plans" />;

  return (
    <div className="space-y-5">
      <SettingsSectionHeader
        eyebrow="Account"
        title="Billing"
        description="Choose a plan to unlock live invoice imports, seats, ERP sync, and recovery. Subscription state is confirmed by signed webhooks."
      />

      {subscription ? (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-panel-2/60 px-5 py-4">
          <p className="text-sm text-ink-2">Current subscription</p>
          <span className="rounded-full bg-accent-soft px-2.5 py-1 text-xs font-semibold capitalize text-accent">
            {subscription.status}
          </span>
        </div>
      ) : null}

      {plans.length ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {plans.map((plan) => {
            const selected = selectedPlan === plan.slug;
            return (
              <article
                key={plan.slug}
                className={`flex flex-col rounded-xl border bg-panel p-5 transition ${
                  selected ? "border-accent ring-1 ring-accent/30" : "border-line"
                }`}
              >
                <h3 className="text-sm font-semibold text-ink">{plan.name}</h3>
                <p className="mt-2 text-2xl font-semibold tabular-nums text-ink">
                  ₹{(plan.amount_paise / 100).toLocaleString("en-IN")}
                  <span className="text-sm font-normal text-ink-4"> / month</span>
                </p>
                <ul className="mt-3 space-y-1 text-sm text-ink-3">
                  <li>{plan.included_active_invoices.toLocaleString("en-IN")} active invoices</li>
                  <li>{plan.included_seats} seats</li>
                </ul>
                <button
                  type="button"
                  onClick={() => setSelectedPlan(selected ? null : plan.slug)}
                  disabled={busy !== null}
                  aria-pressed={selected}
                  className={`mt-5 w-full ${selected ? primaryButtonClass : secondaryButtonClass}`}
                >
                  {selected ? "Selected" : "Choose plan"}
                </button>
              </article>
            );
          })}
        </div>
      ) : !error ? (
        <p className="text-sm text-ink-4">Loading plans…</p>
      ) : null}

      {selectedPlan ? (
        <SettingsCard
          title={`Confirm change to ${selectedPlan}`}
          hint="Re-entering your password authorises the subscription change."
        >
          <form onSubmit={choosePlan} className="flex flex-wrap items-end gap-3">
            <label className={`min-w-64 flex-1 ${labelClass}`}>
              Confirm current password
              <input name="password" type="password" required autoComplete="current-password" className={fieldClass} />
            </label>
            <button disabled={busy !== null} className={primaryButtonClass}>
              {busy ? "Starting checkout…" : "Continue"}
            </button>
            <button type="button" onClick={() => setSelectedPlan(null)} className={secondaryButtonClass}>
              Cancel
            </button>
          </form>
        </SettingsCard>
      ) : null}

      {error ? <SettingsAlert tone="error">{error}</SettingsAlert> : null}
    </div>
  );
}
