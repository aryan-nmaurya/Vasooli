"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

/** End the dashboard session without exposing or manipulating the httpOnly cookie. */
export function SignOutButton({ signedIn }: { signedIn: boolean }) {
  const pathname = usePathname();
  const router = useRouter();
  const containerRef = useRef<HTMLSpanElement>(null);
  const [state, setState] = useState<"idle" | "signing_out" | "failed">("idle");
  const [confirming, setConfirming] = useState(false);
  const busy = state === "signing_out";
  const failed = state === "failed";

  useEffect(() => {
    if (!confirming) return;

    function dismissOnOutsideClick(event: PointerEvent) {
      const target = event.target;
      if (target instanceof Node && !containerRef.current?.contains(target)) {
        setConfirming(false);
      }
    }

    function dismissOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setConfirming(false);
    }

    document.addEventListener("pointerdown", dismissOnOutsideClick);
    document.addEventListener("keydown", dismissOnEscape);
    return () => {
      document.removeEventListener("pointerdown", dismissOnOutsideClick);
      document.removeEventListener("keydown", dismissOnEscape);
    };
  }, [confirming]);

  if (!signedIn || pathname === "/login") return null;

  function requestSignOut() {
    if (busy) return;
    setState("idle");
    setConfirming(true);
  }

  async function signOut() {
    setConfirming(false);
    setState("signing_out");
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
      setState("failed");
    }
  }

  return (
    <span ref={containerRef} className="relative inline-flex">
      <button
        type="button"
        onClick={requestSignOut}
        disabled={busy}
        aria-label={failed ? "Sign out failed — try again" : "Sign out"}
        aria-expanded={confirming}
        title={failed ? "Sign out failed — try again" : "Sign out"}
        className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line bg-panel px-2.5 text-xs text-ink-3 transition hover:bg-panel-2 hover:text-ink disabled:opacity-50"
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
        <span className="hidden sm:inline">
          {state === "signing_out" ? "Signing out…" : "Sign out"}
        </span>
      </button>

      {confirming ? (
        <div
          role="dialog"
          aria-label="Confirm sign out"
          className="absolute right-0 top-[calc(100%+0.5rem)] z-50 w-64 rounded-xl border border-line bg-panel p-3 shadow-xl"
        >
          <p className="text-sm font-medium text-ink">Sign out of Vasooli?</p>
          <p className="mt-1 text-xs leading-relaxed text-ink-3">
            Your dashboard session will end on this device.
          </p>
          <div className="mt-3 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setConfirming(false)}
              className="rounded-md px-2.5 py-1.5 text-xs text-ink-2 ring-1 ring-inset ring-line transition hover:bg-panel-2 hover:text-ink"
            >
              No, stay signed in
            </button>
            <button
              type="button"
              onClick={() => void signOut()}
              className="rounded-md bg-invert px-2.5 py-1.5 text-xs font-medium text-invert-ink transition hover:opacity-90"
            >
              Yes, sign out
            </button>
          </div>
        </div>
      ) : null}
    </span>
  );
}
