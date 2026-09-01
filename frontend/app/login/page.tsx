"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

function DemoSignIn() {
  const router = useRouter();
  const params = useSearchParams();
  const [reviewerAccess, setReviewerAccess] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    fetch("/api/auth")
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (alive && data) setReviewerAccess(Boolean(data.reviewer_access)); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  async function signIn(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        if (response.status === 429) setError("Too many attempts. Wait a minute and try again.");
        else if (response.status >= 500) setError("The demo server is temporarily unavailable.");
        else if (body.reviewer) setError("Demo access is not available on this deployment.");
        else setError("Incorrect username or password.");
        return;
      }
      router.replace(params.get("next") || "/");
      router.refresh();
    } catch {
      setError("Could not reach the demo server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page login-panel">
      <div>
        <section className="auth-card" aria-labelledby="demo-login-title">
          <div className="auth-step">Guided product demo</div>
          <h1 id="demo-login-title">Explore Vasooli.</h1>
          <p className="auth-intro">Open the real dashboard with a seeded ledger, synthetic customers, and Razorpay test mode. No live money or customer contact.</p>

          {reviewerAccess ? (
            <div className="auth-form">
              <button type="button" onClick={() => void signIn({ reviewer: true })} disabled={busy}>{busy ? "Opening demo…" : "Open the demo — no sign-in"}</button>
              <p className="auth-assurance">Read-only access · Every write is refused · No credentials required</p>
            </div>
          ) : null}

          <details className="auth-optional demo-credentials" open={!reviewerAccess}>
            <summary>{reviewerAccess ? "Have demo credentials?" : "Sign in with demo credentials"}</summary>
            <form onSubmit={(event) => { event.preventDefault(); void signIn({ username, password }); }} className="auth-form">
              <label>Demo username<input type="text" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value.replace(/[^A-Za-z0-9_-]/g, "").toLowerCase())} required /></label>
              <label>Password<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
              <button type="submit" disabled={busy || !password || username.length < 2}>{busy ? "Signing in…" : "Sign in to demo"}</button>
            </form>
          </details>

          {error ? <p role="alert" className="auth-error">{error}</p> : null}
        </section>
        <div className="auth-switch auth-switch-links"><Link href="/">About Vasooli</Link><Link href="/guide">Reviewer guide</Link><Link href="/pricing">Pricing</Link></div>
      </div>
    </main>
  );
}

export default function LoginPage() {
  return <Suspense fallback={null}><DemoSignIn /></Suspense>;
}
