"use client";

import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";

/** End the dashboard session without exposing or manipulating the httpOnly cookie. */
export function SignOutButton({ signedIn }: { signedIn: boolean }) {
  const pathname = usePathname();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  if (!signedIn || pathname === "/login") return null;

  async function signOut() {
    setBusy(true);
    setFailed(false);
    try {
      const response = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "logout" }),
      });
      if (!response.ok) throw new Error("logout failed");
      router.replace("/login");
      router.refresh();
    } catch {
      setFailed(true);
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      onClick={signOut}
      disabled={busy}
      aria-label={failed ? "Sign out failed — try again" : "Sign out"}
      title={failed ? "Sign out failed — try again" : "Sign out"}
      className="inline-flex items-center gap-1.5 rounded-md border border-line p-1.5 text-xs text-ink-3 transition hover:bg-panel-2 hover:text-ink disabled:opacity-50 sm:px-2.5"
    >
      <svg
        aria-hidden
        width="15"
        height="15"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M10 17l5-5-5-5" />
        <path d="M15 12H3" />
        <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" />
      </svg>
      <span className="hidden sm:inline">{busy ? "Signing out…" : "Sign out"}</span>
    </button>
  );
}
