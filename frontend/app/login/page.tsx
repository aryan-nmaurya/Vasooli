"use client";

/**
 * The front door, with two of them.
 *
 * Vasooli is one codebase serving two audiences: a merchant signing in to chase their
 * own receivables, and a reviewer who wants to see the product work without being
 * handed a credential. Those wants are different enough that one form serving both
 * ends up serving neither — the merchant wonders which username is theirs, and the
 * reviewer hits a password wall with no way through.
 *
 * So both doors are on the page, labelled, side by side. Neither is hidden behind the
 * other, and which ones appear is asked of the server rather than assumed: a button
 * that can only 404 is worse than no button.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useEffect, useState } from "react";

import { loginLive } from "@/lib/live-api";

type Modes = { reviewer_access: boolean; live_registration: boolean };

function SignIn() {
  const router = useRouter();
  const params = useSearchParams();
  const [modes, setModes] = useState<Modes>({ reviewer_access: false, live_registration: false });

  useEffect(() => {
    let alive = true;
    fetch("/api/auth")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (alive && data) {
          setModes({
            reviewer_access: Boolean(data.reviewer_access),
            live_registration: Boolean(data.live_registration),
          });
        }
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-14">
      <div className="text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Sign in to Vasooli</h1>
        <p className="mx-auto mt-2 max-w-lg text-sm leading-relaxed text-ink-3">
          Two ways in. Use your workspace if you have one — or open the demo and see the
          whole recovery loop running on a seeded ledger, no credential needed.
        </p>
      </div>

      <div className="mt-9 grid gap-4 md:grid-cols-2">
        <LiveDoor showRegister={modes.live_registration} />
        <DemoDoor
          reviewerAccess={modes.reviewer_access}
          next={params.get("next")}
          router={router}
        />
      </div>

      <div className="mt-8 flex flex-wrap items-center justify-center gap-x-5 gap-y-2 text-xs text-ink-3">
        <span>New here?</span>
        <Link href="/" className="text-ink-2 underline-offset-2 hover:text-ink hover:underline">
          What is Vasooli?
        </Link>
        <Link href="/guide" className="text-ink-2 underline-offset-2 hover:text-ink hover:underline">
          Reviewer guide
        </Link>
        <Link href="/pricing" className="text-ink-2 underline-offset-2 hover:text-ink hover:underline">
          Pricing
        </Link>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Door one: a real merchant workspace.                                */
/* ------------------------------------------------------------------ */

function LiveDoor({ showRegister }: { showRegister: boolean }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const data = new FormData(event.currentTarget);
    try {
      const result = await loginLive(
        String(data.get("email")),
        String(data.get("password")),
        String(data.get("otp") || "") || undefined,
      );
      const merchant = result.merchants[0];
      if (!merchant) {
        setError("This account has no active workspace membership yet.");
        return;
      }
      window.localStorage.setItem("vasooli_live_merchant", merchant);
      router.push("/live");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Sign in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col rounded-xl border border-line bg-panel p-6 shadow-sm">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold text-ink">Your workspace</h2>
        <span className="rounded bg-accent-soft px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-accent">
          Live
        </span>
      </div>
      <p className="mt-1.5 text-xs leading-relaxed text-ink-3">
        Your own ledger, your own Razorpay account, your own customers.
      </p>

      <form onSubmit={submit} className="mt-5 flex flex-col gap-2.5">
        <input
          name="email"
          type="email"
          required
          autoComplete="email"
          placeholder="Work email"
          aria-label="Work email"
          className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-ink-4"
        />
        <input
          name="password"
          type="password"
          required
          autoComplete="current-password"
          placeholder="Password"
          aria-label="Password"
          className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-ink-4"
        />
        <input
          name="otp"
          inputMode="numeric"
          pattern="[0-9]{6}"
          maxLength={6}
          placeholder="Authenticator code (if enabled)"
          aria-label="Authenticator code"
          className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-ink-4"
        />
        <button
          type="submit"
          disabled={busy}
          className="mt-1 w-full rounded-md bg-invert px-3 py-2 text-sm font-medium text-invert-ink transition hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {error ? (
          <p role="alert" className="text-xs leading-relaxed text-rose-700 dark:text-rose-300">
            {error}
          </p>
        ) : null}
      </form>

      <div className="mt-auto pt-5 text-xs text-ink-3">
        {showRegister ? (
          <>
            No workspace yet?{" "}
            <Link
              href="/register"
              className="font-medium text-ink-2 underline-offset-2 hover:text-ink hover:underline"
            >
              Create one
            </Link>
          </>
        ) : (
          <span>Self-serve signup is currently closed on this deployment.</span>
        )}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Door two: the seeded demo, including the no-credential reviewer path.*/
/* ------------------------------------------------------------------ */

function DemoDoor({
  reviewerAccess,
  next,
  router,
}: {
  reviewerAccess: boolean;
  next: string | null;
  router: ReturnType<typeof useRouter>;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function post(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        // Distinguish the causes. Reporting "incorrect password" when the server is
        // simply down sends someone hunting for a typo that isn't there — a terrible
        // way to spend the minutes before a demo.
        if (res.status === 429) setError("Too many attempts. Wait a minute and try again.");
        else if (res.status >= 500) setError("Cannot reach the server. Is the backend running?");
        else if (body.reviewer) setError("Demo access is not available on this deployment.");
        else setError("Incorrect username or password.");
        return;
      }
      router.replace(next || "/");
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col rounded-xl border border-line bg-panel p-6 shadow-sm">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold text-ink">Explore the demo</h2>
        <span className="rounded bg-panel-2 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-ink-3">
          Seeded
        </span>
      </div>
      <p className="mt-1.5 text-xs leading-relaxed text-ink-3">
        The real dashboard over a seeded ledger — synthetic customers, Razorpay test
        mode, no real money.
      </p>

      {/* The reviewer path leads, because most people arriving here have no
          credential and the alternative — mailing a shared password around — puts a
          real one in an inbox and gives everyone the same one. This opens a session
          on the read-only auditor role instead; the backend refuses every write. */}
      {reviewerAccess ? (
        <div className="mt-5">
          <button
            onClick={() => post({ reviewer: true })}
            disabled={busy}
            className="w-full rounded-md border border-line px-3 py-2 text-sm font-medium text-ink transition hover:bg-panel-2 disabled:opacity-50"
          >
            {busy ? "Opening…" : "Open the demo — no sign-in"}
          </button>
          <p className="mt-2 text-[11px] leading-relaxed text-ink-3">
            Read-only. Every write — sending, importing, recording a payment, resolving
            a dispute — is refused for this session.
          </p>
        </div>
      ) : null}

      <details className="group mt-5">
        <summary className="cursor-pointer list-none text-xs text-ink-3 transition hover:text-ink">
          <span className="underline-offset-2 group-open:hidden">
            Have demo credentials? Sign in →
          </span>
          <span className="hidden group-open:inline">Demo credentials</span>
        </summary>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void post({ username, password });
          }}
          className="mt-3 flex flex-col gap-2.5"
        >
          <input
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) =>
              setUsername(e.target.value.replace(/[^A-Za-z0-9_-]/g, "").toLowerCase())
            }
            placeholder="Username"
            aria-label="Demo username"
            className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-ink-4"
          />
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            aria-label="Demo password"
            className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-ink-4"
          />
          <button
            type="submit"
            disabled={busy || !password || username.length < 2}
            className="w-full rounded-md border border-line px-3 py-2 text-sm font-medium text-ink transition hover:bg-panel-2 disabled:opacity-50"
          >
            {busy ? "Signing in…" : "Sign in to demo"}
          </button>
        </form>
      </details>

      {error ? (
        <p role="alert" className="mt-3 text-xs leading-relaxed text-rose-700 dark:text-rose-300">
          {error}
        </p>
      ) : null}
    </section>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <SignIn />
    </Suspense>
  );
}
