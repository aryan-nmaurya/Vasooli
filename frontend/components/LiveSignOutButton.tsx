"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { logoutLive } from "@/lib/live-api";

export function LiveSignOutButton() {
  const router = useRouter();
  const containerRef = useRef<HTMLSpanElement>(null);
  const [merchant, setMerchant] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => { Promise.resolve().then(() => setMerchant(window.localStorage.getItem("vasooli_live_merchant") || "")); }, []);

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

  if (!merchant) return <Link href="/live/login" className="rounded-lg border border-line bg-panel px-3 py-2 text-xs text-ink-2 hover:bg-panel-2">Sign in</Link>;

  async function signOut() {
    setConfirming(false);
    setBusy(true);
    try { await logoutLive(); } catch { /* Clear local workspace selection even if the server session expired. */ }
    window.localStorage.removeItem("vasooli_live_merchant");
    router.replace("/live/login");
    router.refresh();
  }

  return (
    <span ref={containerRef} className="relative inline-flex">
      <button
        type="button"
        disabled={busy}
        onClick={() => setConfirming(true)}
        aria-expanded={confirming}
        className="rounded-lg border border-line bg-panel px-3 py-2 text-xs text-ink-2 hover:bg-panel-2 disabled:opacity-50"
      >
        {busy ? "Signing out…" : "Sign out"}
      </button>

      {confirming ? (
        <div
          role="dialog"
          aria-label="Confirm sign out"
          className="absolute right-0 top-[calc(100%+0.5rem)] z-50 w-64 rounded-xl border border-line bg-panel p-3 shadow-xl"
        >
          <p className="text-sm font-medium text-ink">Sign out of Vasooli?</p>
          <p className="mt-1 text-xs leading-relaxed text-ink-3">
            Your live workspace session will end on this device.
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
