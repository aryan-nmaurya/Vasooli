"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { loginLive, registerLive, verifyLive } from "@/lib/live-api";

export function LiveRegistrationForm() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    const data = new FormData(event.currentTarget);
    try {
      const result = await registerLive({
        email: String(data.get("email")),
        password: String(data.get("password")),
        legal_business_name: String(data.get("business")),
        country: String(data.get("country") || "IN"),
        timezone: String(data.get("timezone") || "Asia/Kolkata"),
        accept_terms: data.get("terms") === "on",
        accept_privacy: data.get("privacy") === "on",
      });
      if (result.verification_token) {
        await verifyLive(result.verification_token);
        const login = await loginLive(String(data.get("email")), String(data.get("password")));
        window.localStorage.setItem("vasooli_live_merchant", login.merchants[0]);
        setMessage("Email verified. Your live workspace is ready; continue to billing.");
        router.push(`/live?merchant=${login.merchants[0]}`);
      } else {
        setMessage("Check your inbox to verify your email, then return here to continue.");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mx-auto max-w-xl space-y-4 rounded-2xl border border-line bg-panel p-6 shadow-sm">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Start a live workspace</h1>
        <p className="mt-1 text-sm text-ink-3">Create your secure merchant workspace and verify your work email.</p>
      </div>
      <label className="block text-sm">Business name<input name="business" required minLength={2} className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label>
      <label className="block text-sm">Work email<input name="email" type="email" required className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label>
      <label className="block text-sm">Password<input name="password" type="password" minLength={12} required className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-sm">Country<input name="country" defaultValue="IN" pattern="[A-Z]{2}" className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label>
        <label className="block text-sm">Timezone<input name="timezone" defaultValue="Asia/Kolkata" className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label>
      </div>
      <label className="flex gap-2 text-xs text-ink-3"><input name="terms" type="checkbox" required /> <span>I accept the <a href="/terms" className="text-accent underline">terms</a>.</span></label>
      <label className="flex gap-2 text-xs text-ink-3"><input name="privacy" type="checkbox" required /> <span>I accept the <a href="/privacy" className="text-accent underline">privacy policy</a>.</span></label>
      <p className="text-xs text-ink-4">Business data is processed under the <a href="/dpa" className="text-accent underline">Data Processing Addendum</a>.</p>
      {error ? <p role="alert" className="rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">{error}</p> : null}
      {message ? <p className="rounded-lg bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700">{message}</p> : null}
      <button disabled={busy} className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">{busy ? "Creating workspace…" : "Create live workspace"}</button>
    </form>
  );
}
