"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

import { API_BASE, loginLive, registerLive, verifyLiveCode } from "@/lib/live-api";
import type { LiveRegistrationPayload } from "@/lib/live-api";

type PlanChoice = {
  slug: string;
  name: string;
  amount_paise: number;
  included_active_invoices: number;
  included_seats: number;
};

/** Matches LIVE_TRIAL_DAYS on the server; the pricing page promises the same. */
const TRIAL_DAYS = 7;

export function LiveRegistrationForm() {
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [terms, setTerms] = useState(false);
  const [privacy, setPrivacy] = useState(false);
  const [phase, setPhase] = useState<"details" | "verify" | "verified">("details");
  const [plans, setPlans] = useState<PlanChoice[]>([]);
  const [chosenPlan, setChosenPlan] = useState<string | null>(null);
  // True when auto sign-in succeeded, so the plan step can go straight to checkout
  // instead of routing back through the login page.
  const [signedIn, setSignedIn] = useState(false);
  const [pendingRegistration, setPendingRegistration] = useState<LiveRegistrationPayload | null>(null);
  const [resendCooldown, setResendCooldown] = useState(0);
  // Only ever set outside production: the API returns the raw code in local/test so
  // the lifecycle stays testable where the sending provider is unreachable.
  const [devCode, setDevCode] = useState<string | null>(null);

  useEffect(() => {
    if (phase !== "verified") return;
    // A public price list, so no session or merchant header is needed — which is
    // what lets it load before the merchant has ever signed in.
    fetch(`${API_BASE}/api/live/billing/plans`)
      .then((r) => (r.ok ? r.json() : []))
      .then((rows: PlanChoice[]) => setPlans(rows))
      .catch(() => setPlans([]));
  }, [phase]);

  useEffect(() => {
    if (phase !== "verify" || resendCooldown <= 0) return;
    const timer = window.setTimeout(() => setResendCooldown((value) => value - 1), 1_000);
    return () => window.clearTimeout(timer);
  }, [phase, resendCooldown]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    const data = new FormData(event.currentTarget);
    const payload: LiveRegistrationPayload = {
      email: String(data.get("email")).trim().toLowerCase(),
      password: String(data.get("password")),
      legal_business_name: String(data.get("business")),
      country: String(data.get("country") || "IN"),
      timezone: String(data.get("timezone") || "Asia/Kolkata"),
      accept_terms: data.get("terms") === "on",
      accept_privacy: data.get("privacy") === "on",
    };
    try {
      const result = await registerLive(payload);
      setPendingRegistration(payload);
      setPhase("verify");
      setResendCooldown(30);
      setDevCode(result.verification_token ?? null);
      // A code is NOT always issued. Registering an address that already exists and
      // is verified deliberately sends nothing, so the API can't be used to test who
      // holds an account — but the old copy claimed a code had been sent regardless,
      // which left someone waiting for mail that was never going to arrive. This
      // wording is true either way, and points at the door they actually need.
      setMessage(
        result.verification_token
          ? `Development mode — no email was sent. Your code is ${result.verification_token}.`
          : `If ${payload.email} still needs verifying, a six-digit code is on its way. Already have a workspace? Sign in instead.`,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  }

  async function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const data = new FormData(event.currentTarget);
    if (!pendingRegistration) {
      setBusy(false);
      setError("Your registration session expired. Please enter your details again.");
      setPhase("details");
      return;
    }
    try {
      await verifyLiveCode(pendingRegistration.email, String(data.get("code")));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Verification failed");
      setBusy(false);
      return;
    }
    try {
      const login = await loginLive(pendingRegistration.email, pendingRegistration.password);
      const merchant = login.merchants[0];
      if (!merchant) throw new Error("Your workspace membership could not be found.");
      window.localStorage.setItem("vasooli_live_merchant", merchant);
      // Signed in, but a brand-new workspace has no subscription, so the dashboard
      // would only bounce back here on the first write. Ask for the plan now.
      setSignedIn(true);
      setPhase("verified");
      setMessage("Email verified. One step left before your workspace opens.");
    } catch {
      // The OTP is single-use. If verification committed but session creation or
      // navigation failed, never leave the user trapped on a consumed code.
      setPhase("verified");
      setMessage("Your email is verified. Sign in to continue to your workspace.");
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    if (!pendingRegistration || busy || resendCooldown > 0) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await registerLive(pendingRegistration);
      setResendCooldown(30);
      setDevCode(result.verification_token ?? null);
      setMessage(
        result.verification_token
          ? `Development mode — no email was sent. Your new code is ${result.verification_token}.`
          : "If that address still needs verifying, a fresh code is on its way. Earlier codes no longer work.",
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not resend the code");
    } finally {
      setBusy(false);
    }
  }

  if (phase === "verified") {
    return (
      <section className="auth-card" aria-labelledby="verified-title">
        <div className="auth-step">Email verified</div>
        <h1 id="verified-title">Choose your plan.</h1>
        <p className="auth-intro">
          Every plan starts with a {TRIAL_DAYS}-day free trial. Your card is not charged
          until the trial ends, and you can cancel at any time.
        </p>

        {/*
          Styled with utilities rather than new .auth-* rules: globals.css is a
          frozen demo source, and re-baselining the demo freeze for a live-auth
          screen the demo never renders would be the wrong trade.
        */}
        <div className="mt-4 grid gap-2.5">
          {plans.map((plan) => {
            const picked = chosenPlan === plan.slug;
            return (
              <button
                type="button"
                key={plan.slug}
                aria-pressed={picked}
                onClick={() => setChosenPlan(plan.slug)}
                className={`flex w-full flex-col items-start gap-1 rounded-lg border px-4 py-3 text-left transition ${
                  picked
                    ? "border-[#55c7d6] bg-[#55c7d6]/10"
                    : "border-[rgba(243,241,234,.16)] hover:border-[rgba(243,241,234,.32)]"
                }`}
              >
                <span className="flex w-full flex-wrap items-baseline gap-x-2">
                  <span className="text-sm font-semibold text-[#f3f1ea]">{plan.name}</span>
                  <span className="ml-auto text-sm font-semibold tabular-nums text-[#f3f1ea]">
                    ₹{(plan.amount_paise / 100).toLocaleString("en-IN")}
                    <span className="font-normal text-[#aeb2ac]"> / month</span>
                  </span>
                </span>
                <span className="text-xs leading-5 text-[#aeb2ac]">
                  {plan.included_active_invoices.toLocaleString("en-IN")} active invoices ·{" "}
                  {plan.included_seats === 1 ? "1 user" : `up to ${plan.included_seats} users`}
                </span>
              </button>
            );
          })}
        </div>

        <p role="status" className="auth-success" style={{ marginTop: "1.25rem" }}>
          {chosenPlan
            ? "A ₹2 charge confirms your Autopay mandate and is refunded automatically. Your plan is not charged until the trial ends."
            : signedIn
              ? "Pick a plan to open your workspace."
              : "Pick a plan to continue. You confirm payment right after signing in."}
        </p>

        <Link
          className="auth-primary-link"
          // Checkout needs a session. When auto sign-in worked we already have one,
          // so go straight to billing; otherwise the choice rides through the login
          // page in storage and billing pre-selects it on arrival.
          href={signedIn ? "/live/settings/billing?reason=new_signup" : "/live/login"}
          onClick={() => {
            if (chosenPlan) window.localStorage.setItem("vasooli_pending_plan", chosenPlan);
          }}
          aria-disabled={!chosenPlan}
          style={chosenPlan ? undefined : { opacity: 0.45, pointerEvents: "none" }}
        >
          {signedIn ? "Continue to payment" : "Continue to secure sign in"}
        </Link>
      </section>
    );
  }

  if (phase === "verify") {
    return (
      <section className="auth-card" aria-labelledby="verify-title">
        <div className="auth-step">Step 2 of 2</div>
        <h1 id="verify-title">Verify your work email</h1>
        <p className="auth-intro">Enter the one-time code sent to <strong>{pendingRegistration?.email}</strong>. It expires after 15 minutes and can only be used once.</p>
        <form onSubmit={verify} className="auth-form">
          {/*
            `key` is tied to devCode so React remounts the input when a new code
            arrives — defaultValue alone is read once and a resend would leave the
            previous code sitting in the box.
          */}
          <label>Six-digit code<input key={devCode ?? "verification-code"} defaultValue={devCode ?? ""} name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required className="auth-code" /></label>
          {error ? <p role="alert" className="auth-error">{error}</p> : null}
          {message ? <p role="status" className="auth-success">{message}</p> : null}
          <button disabled={busy}>{busy ? "Verifying…" : "Verify email and continue"}</button>
          <button type="button" className="auth-secondary" disabled={busy || resendCooldown > 0} onClick={resend}>
            {resendCooldown > 0 ? `Resend code in ${resendCooldown}s` : "Resend code"}
          </button>
          <button type="button" className="auth-secondary" onClick={() => { setPhase("details"); setPendingRegistration(null); setError(null); setMessage(null); setDevCode(null); }}>Use a different email</button>
          <Link className="auth-secondary" href="/live/login" style={{ textAlign: "center" }}>Already verified? Sign in</Link>
        </form>
      </section>
    );
  }

  return (
    <section className="auth-card" aria-labelledby="register-title">
      <div className="auth-step">Step 1 of 2 · Starter · 7-day trial</div>
      <h1 id="register-title">Put overdue revenue back in motion.</h1>
      <p className="auth-intro">Create your merchant workspace. No card required; your email must be verified before sign-in.</p>
      <form onSubmit={submit} className="auth-form">
        <label>Business name<input name="business" autoComplete="organization" required minLength={2} /></label>
        <label>Work email<input name="email" type="email" autoComplete="email" inputMode="email" required /></label>
        <label>Password<input name="password" type="password" autoComplete="new-password" minLength={12} pattern="(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9]).{12,}" title="At least 12 characters with uppercase, lowercase, and a number" required /></label>
        <p className="auth-helper">At least 12 characters with uppercase, lowercase, and a number.</p>
        <input name="country" type="hidden" value="IN" readOnly /><input name="timezone" type="hidden" value="Asia/Kolkata" readOnly />
        <label className="auth-check"><input name="terms" type="checkbox" checked={terms} onChange={(event) => setTerms(event.target.checked)} /><span>I agree to the <Link href="/terms" target="_blank">Terms of Service</Link>.</span></label>
        <label className="auth-check"><input name="privacy" type="checkbox" checked={privacy} onChange={(event) => setPrivacy(event.target.checked)} /><span>I have read the <Link href="/privacy" target="_blank">Privacy Policy</Link>.</span></label>
        <p className="auth-helper">Business data is handled under our <Link href="/dpa">Data Processing Addendum</Link>.</p>
        {error ? <p role="alert" className="auth-error">{error}</p> : null}
        <button disabled={busy || !terms || !privacy}>{busy ? "Creating your workspace…" : "Create workspace and verify email"}</button>
        <p className="auth-assurance">Encrypted credentials · Tenant-scoped records · Audited actions</p>
      </form>
    </section>
  );
}
