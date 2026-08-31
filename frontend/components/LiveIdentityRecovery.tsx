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

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const data = new FormData(event.currentTarget);
      const result = await forgotPasswordLive(String(data.get("email")));
      setMessage(result.message);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <IdentityCard title="Reset your password">
      <p className="mb-5 text-sm text-ink-3">Enter your work email. We will send a time-limited reset link if an account exists.</p>
      <form onSubmit={submit} className="space-y-4">
        <label className="block text-sm">Work email<input name="email" type="email" required className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label>
        {message ? <p className="text-sm text-emerald-700">{message}</p> : null}
        {error ? <p role="alert" className="text-sm text-rose-700">{error}</p> : null}
        <button disabled={busy} className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">{busy ? "Sending…" : "Send reset link"}</button>
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
    <IdentityCard title="Choose a new password">
      <form onSubmit={submit} className="space-y-4">
        <label className="block text-sm">New password<input name="password" type="password" minLength={12} required className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label>
        <label className="block text-sm">Confirm password<input name="confirm" type="password" minLength={12} required className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label>
        <p className="text-xs text-ink-4">Use at least 12 characters with uppercase, lowercase, and a number.</p>
        {message ? <p className="text-sm text-emerald-700">{message} <Link href="/live/login" className="underline">Sign in</Link></p> : null}
        {error ? <p role="alert" className="text-sm text-rose-700">{error}</p> : null}
        <button disabled={busy || !token} className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">{busy ? "Saving…" : "Save new password"}</button>
      </form>
    </IdentityCard>
  );
}

function IdentityCard({ title, children }: { title: string; children: React.ReactNode }) {
  return <main className="mx-auto max-w-md px-4 py-14 sm:px-6"><section className="rounded-2xl border border-line bg-panel p-6 shadow-sm"><h1 className="mb-3 text-2xl font-semibold tracking-tight">{title}</h1>{children}</section></main>;
}
