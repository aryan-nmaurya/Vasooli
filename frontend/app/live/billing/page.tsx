"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";

import { LiveSignInPrompt } from "@/components/LiveSignInPrompt";

import { liveGet, livePost, reauthLive } from "@/lib/live-api";

type Plan = { slug: string; name: string; amount_paise: number; included_active_invoices: number; included_seats: number };
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
    ]).then(([loadedPlans, loadedSubscription]) => {
      setPlans(loadedPlans);
      setSubscription(loadedSubscription);
    }).catch((cause) => setError(cause instanceof Error ? cause.message : "Unable to load billing"));
  }, []);
  async function choosePlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const slug = selectedPlan;
    if (!slug) return;
    if (!merchant) { setError("Sign in to a live workspace first."); return; }
    const password = String(new FormData(event.currentTarget).get("password"));
    setBusy(slug); setError(null);
    try {
      const proof = await reauthLive(password);
      const result = await livePost<{ status: string; plan: string }>("/api/live/billing/checkout", merchant, { plan_slug: slug }, { "X-Reauth-Token": proof.reauth_token });
      setSubscription((current) => ({ ...(current || { plan_id: "", provider_subscription_id: null }), status: result.status, plan_id: result.plan, provider_subscription_id: null }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Checkout could not be started"); }
    finally { setBusy(null); }
  }
  return <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6"><Link href="/live" className="text-sm text-accent">← Setup</Link><h1 className="mt-5 text-3xl font-semibold">Billing</h1><p className="mt-3 max-w-xl text-ink-3">Choose a plan to unlock live invoice imports, seats, ERP sync, and recovery. Subscription state is confirmed by signed webhooks.</p>{subscription ? <p className="mt-5 rounded-lg bg-accent/10 px-4 py-3 text-sm">Current subscription status: <strong>{subscription.status}</strong></p> : null}<div className="mt-8 grid gap-4 sm:grid-cols-3">{plans.map((plan) => <article key={plan.slug} className={`rounded-2xl border bg-panel p-5 ${selectedPlan === plan.slug ? "border-accent" : "border-line"}`}><h2 className="text-lg font-semibold">{plan.name}</h2><p className="mt-2 text-2xl font-semibold">₹{(plan.amount_paise / 100).toLocaleString("en-IN")}<span className="text-sm font-normal text-ink-4"> / month</span></p><p className="mt-3 text-sm text-ink-3">{plan.included_active_invoices.toLocaleString("en-IN")} active invoices · {plan.included_seats} seats</p><button type="button" onClick={() => setSelectedPlan(plan.slug)} disabled={busy !== null} className="mt-5 rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">{selectedPlan === plan.slug ? "Selected" : "Choose plan"}</button></article>)}</div>{selectedPlan ? <form onSubmit={choosePlan} className="mt-5 flex flex-wrap items-end gap-3 rounded-2xl border border-line bg-panel p-5"><label className="min-w-64 flex-1 text-sm">Confirm current password<input name="password" type="password" required autoComplete="current-password" className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label><button disabled={busy !== null} className="rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">{busy ? "Starting checkout…" : `Continue with ${selectedPlan}`}</button><button type="button" onClick={() => setSelectedPlan(null)} className="rounded-lg border border-line px-4 py-2.5 text-sm">Cancel</button></form> : null}{!merchant ? <LiveSignInPrompt what="Billing and plans" /> : !plans.length && !error ? <p className="mt-8 text-sm text-ink-4">Loading plans…</p> : null}{error ? <p role="alert" className="mt-5 rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">{error}</p> : null}</main>;
}
