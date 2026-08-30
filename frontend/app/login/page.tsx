"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  //: Whether this deployment offers a read-only reviewer session. Asked rather than
  //: assumed: rendering a button that can only 404 is worse than rendering none.
  const [reviewerAccess, setReviewerAccess] = useState(false);

  useEffect(() => {
    let alive = true;
    fetch("/api/auth")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (alive && data) setReviewerAccess(Boolean(data.reviewer_access));
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  async function continueAsReviewer() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reviewer: true }),
      });
      if (!res.ok) {
        setError("Reviewer access is not available on this deployment right now.");
        return;
      }
      router.replace(params.get("next") || "/");
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password, username }),
      });
      if (!res.ok) {
        // Distinguish the causes. Reporting "incorrect password" when the server is
        // simply down sends someone hunting for a typo that isn't there — which is a
        // terrible way to spend the minutes before a demo.
        if (res.status === 429) {
          setError("Too many attempts. Wait a minute and try again.");
        } else if (res.status >= 500) {
          setError("Cannot reach the server. Is the backend running?");
        } else {
          setError("Incorrect username or password.");
        }
        return;
      }
      router.replace(params.get("next") || "/");
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-panel mx-auto mt-24 max-w-sm">
      <h1 className="text-lg font-semibold text-ink">Vasooli</h1>
      <p className="mt-1 text-sm text-ink-3">
        This dashboard shows customer and payment data. Sign in to continue.
      </p>

      <form onSubmit={submit} className="mt-6 space-y-3">
        <input
          type="text"
          autoFocus
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value.replace(/[^A-Za-z0-9_-]/g, "").toLowerCase())}
          placeholder="Username"
          aria-label="Username"
          className="w-full rounded-lg border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-ink-4"
        />
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          className="w-full rounded-lg border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-ink-4"
        />
        <button
          type="submit"
          disabled={busy || !password || username.length < 2}
          className="w-full rounded-md bg-invert px-3 py-2 text-sm font-medium text-invert-ink transition hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {error ? (
          <p className="text-xs text-rose-700 dark:text-rose-300">{error}</p>
        ) : null}
      </form>

      {/* A reviewer arriving from the public site previously hit this wall with no way
          through, which made a working system look like a dead demo. The alternative —
          mailing a shared password around — puts a real credential in an inbox and
          gives everyone the same one. This opens a session on the read-only auditor
          role instead; the backend refuses every write for it. */}
      {reviewerAccess ? (
        <div className="mt-5 border-t border-line pt-4">
          <button
            onClick={continueAsReviewer}
            disabled={busy}
            className="w-full rounded-md border border-line px-3 py-2 text-sm font-medium text-ink transition hover:bg-panel-2 disabled:opacity-50"
          >
            Continue as reviewer (read-only)
          </button>
          <p className="mt-2 text-[11px] leading-relaxed text-ink-3">
            Opens the real dashboard over the seeded demo ledger — synthetic customers
            on invented domains, Razorpay test mode, no real money. Every write —
            sending, importing, recording a payment, resolving a dispute — is refused
            for this session.
          </p>
        </div>
      ) : null}

      {/* A reviewer who arrives here without credentials must not be stuck. Both
          destinations are public and carry no data. */}
      <div className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line pt-4 text-xs text-ink-3">
        <span>Reviewing this project?</span>
        <Link href="/guide" className="text-ink-2 hover:text-ink">
          Read the reviewer guide
        </Link>
        <Link href="/" className="text-ink-2 hover:text-ink">
          What is Vasooli?
        </Link>
      </div>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
