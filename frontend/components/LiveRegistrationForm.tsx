"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { loginLive, registerLive, verifyLiveCode } from "@/lib/live-api";
import type { LiveRegistrationPayload } from "@/lib/live-api";

export function LiveRegistrationForm() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [terms, setTerms] = useState(false);
  const [privacy, setPrivacy] = useState(false);
  const [phase, setPhase] = useState<"details" | "verify" | "verified">("details");
  const [pendingRegistration, setPendingRegistration] = useState<LiveRegistrationPayload | null>(null);
  const [resendCooldown, setResendCooldown] = useState(0);

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
      await registerLive(payload);
      setPendingRegistration(payload);
      setPhase("verify");
      setResendCooldown(30);
      setMessage(`We sent a six-digit verification code to ${payload.email}.`);
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
      setMessage("Email verified. Your secure workspace is ready.");
      router.push("/live");
      router.refresh();
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
      await registerLive(pendingRegistration);
      setResendCooldown(30);
      setMessage("A fresh verification code was sent. Earlier codes no longer work.");
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
        <h1 id="verified-title">Your workspace is ready.</h1>
        <p role="status" className="auth-success">{message}</p>
        <Link className="auth-primary-link" href="/live/login">Continue to secure sign in</Link>
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
          <label>Six-digit code<input key="verification-code" name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required className="auth-code" /></label>
          {error ? <p role="alert" className="auth-error">{error}</p> : null}
          {message ? <p role="status" className="auth-success">{message}</p> : null}
          <button disabled={busy}>{busy ? "Verifying…" : "Verify email and continue"}</button>
          <button type="button" className="auth-secondary" disabled={busy || resendCooldown > 0} onClick={resend}>
            {resendCooldown > 0 ? `Resend code in ${resendCooldown}s` : "Resend code"}
          </button>
          <button type="button" className="auth-secondary" onClick={() => { setPhase("details"); setPendingRegistration(null); setError(null); setMessage(null); }}>Use a different email</button>
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
