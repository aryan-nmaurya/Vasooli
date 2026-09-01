"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

import { forgotPasswordLive, resetPasswordLive, verifyLive } from "@/lib/live-api";

export function VerifyEmail({ token }: { token: string }) {
  const [state, setState] = useState<"working" | "verified" | "failed">("working");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token) {
      Promise.resolve().then(() => {
        setState("failed");
        setError("This verification link is incomplete.");
      });
      return;
    }
    verifyLive(token)
      .then(() => setState("verified"))
      .catch((cause) => {
        setState("failed");
        setError(cause instanceof Error ? cause.message : "Verification failed");
      });
  }, [token]);

  return (
    <IdentityCard title="Verify your email">
      {state === "working" ? <p className="text-sm text-ink-3">Verifying your secure link…</p> : null}
      {state === "verified" ? <p className="text-sm text-emerald-700">Email verified. You can now sign in.</p> : null}
      {state === "failed" ? <p role="alert" className="text-sm text-rose-700">{error}</p> : null}
      <Link href="/live/login" className="mt-5 inline-flex rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white">Open sign in</Link>
    </IdentityCard>
  );
}

export function ForgotPassword() {
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [resetLink, setResetLink] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const data = new FormData(event.currentTarget);
      const result = await forgotPasswordLive(String(data.get("email")));
      setMessage(result.message);
      setResetLink(result.reset_token ? `/reset-password?token=${result.reset_token}` : "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <IdentityCard title="Reset your password" eyebrow="Account recovery">
      <p className="auth-intro">Enter your work email. If an account exists, we will send a secure link that expires in 30 minutes.</p>
      <form onSubmit={submit} className="auth-form">
        <label>Work email<input name="email" type="email" autoComplete="email" inputMode="email" required /></label>
        {message ? <p role="status" className="auth-success">{message}</p> : null}
        {resetLink ? <Link className="auth-local-link" href={resetLink}>Open the local reset form</Link> : null}
        {error ? <p role="alert" className="auth-error">{error}</p> : null}
        <button disabled={busy}>{busy ? "Sending secure link…" : "Send password reset link"}</button>
        <p className="auth-assurance">For your privacy, we show the same confirmation whether or not the email is registered.</p>
      </form>
    </IdentityCard>
  );
}

export function ResetPassword({ token }: { token: string }) {
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const data = new FormData(event.currentTarget);
      const password = String(data.get("password"));
      if (password !== String(data.get("confirm"))) throw new Error("Passwords do not match");
      await resetPasswordLive(token, password);
      setMessage("Password changed. Existing sessions have been revoked.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Reset failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <IdentityCard title="Choose a new password" eyebrow="Secure reset">
      <form onSubmit={submit} className="auth-form">
        <label>New password<input name="password" type="password" autoComplete="new-password" minLength={12} pattern="(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9]).{12,}" required /></label>
        <label>Confirm password<input name="confirm" type="password" autoComplete="new-password" minLength={12} required /></label>
        <p className="auth-helper">Use at least 12 characters with uppercase, lowercase, and a number.</p>
        {message ? <p className="auth-success">{message} <Link href="/live/login">Sign in</Link></p> : null}
        {error ? <p role="alert" className="auth-error">{error}</p> : null}
        <button disabled={busy || !token}>{busy ? "Saving new password…" : "Save new password"}</button>
      </form>
    </IdentityCard>
  );
}

function IdentityCard({ title, eyebrow = "Email verification", children }: { title: string; eyebrow?: string; children: React.ReactNode }) {
  return <main className="auth-page"><section className="auth-card auth-recovery-card"><div className="auth-step">{eyebrow}</div><h1>{title}</h1>{children}</section></main>;
}
