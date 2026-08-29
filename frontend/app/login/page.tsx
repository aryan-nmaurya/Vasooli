"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
    <div className="mx-auto mt-24 max-w-sm">
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
