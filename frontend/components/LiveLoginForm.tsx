"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

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
      router.push("/live");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }
  return <section className="auth-card" aria-labelledby="login-title">
    <div className="auth-step">Secure merchant access</div>
    <h1 id="login-title">Welcome back.</h1>
    <p className="auth-intro">Sign in to continue recovering receivables from the exact point your team left off.</p>
    <form onSubmit={submit} className="auth-form">
      <label>Work email<input name="email" type="email" autoComplete="email" inputMode="email" required /></label>
      <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
      <div className="auth-form-link"><Link href="/forgot-password">Forgot your password?</Link></div>
      <details className="auth-optional"><summary>Use an authenticator code</summary><label>Authenticator code<input name="otp" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6,8}" maxLength={8} /></label></details>
      {error ? <p role="alert" className="auth-error">{error === "Invalid credentials" ? "The email, password, or verification status is incorrect." : error}</p> : null}
      <button disabled={busy}>{busy ? "Signing in securely…" : "Sign in to workspace"}</button>
      <p className="auth-assurance">Your session uses secure, HTTP-only credentials and expires automatically.</p>
    </form>
  </section>;
}
