"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { loginLive } from "@/lib/live-api";

export function LiveLoginForm() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const data = new FormData(event.currentTarget);
    try {
      const result = await loginLive(String(data.get("email")), String(data.get("password")), String(data.get("otp") || "") || undefined);
      const merchant = result.merchants[0];
      if (!merchant) throw new Error("No active merchant membership found");
      window.localStorage.setItem("vasooli_live_merchant", merchant);
      router.push(`/live?merchant=${merchant}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }
  return <form onSubmit={submit} className="mx-auto max-w-md space-y-4 rounded-2xl border border-line bg-panel p-6"><div><h1 className="text-2xl font-semibold">Live sign in</h1><p className="mt-1 text-sm text-ink-3">This is separate from the read-only demo session.</p></div><label className="block text-sm">Email<input name="email" type="email" required className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label><label className="block text-sm">Password<input name="password" type="password" required className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label><label className="block text-sm">Authenticator code <span className="text-ink-4">(if enabled)</span><input name="otp" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label>{error ? <p role="alert" className="rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">{error}</p> : null}<button disabled={busy} className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">{busy ? "Signing in…" : "Sign in to live"}</button></form>;
}
