"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Browser Back can otherwise make a signed-in user think they have left the
 * dashboard while the session remains active. On mount this marks the current
 * history entry as a sentinel and pushes a live entry on top, so the next Back
 * lands on the sentinel and opens the confirmation instead of navigating away.
 *
 * Deliberately not `window.confirm`: the native dialog cannot be styled, and it
 * looks nothing like the in-app sign-out. Because the confirmation is now async,
 * the sentinel is re-armed the moment Back fires — the user stays on the page
 * while deciding, and a later Back is still guarded.
 */
export function ExitGuard({
  title,
  body,
  onConfirm,
}: {
  title: string;
  body: string;
  onConfirm: () => Promise<void> | void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const { href } = window.location;
    window.history.replaceState(
      { ...window.history.state, vasooliExitSentinel: true },
      "",
      href,
    );
    window.history.pushState({ vasooliExitCurrent: true }, "", href);

    function onPopState(event: PopStateEvent) {
      if (!event.state?.vasooliExitSentinel) return;
      window.history.pushState({ vasooliExitCurrent: true }, "", window.location.href);
      setOpen(true);
    }

    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();

    function dismissOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }

    document.addEventListener("keydown", dismissOnEscape);
    return () => document.removeEventListener("keydown", dismissOnEscape);
  }, [open]);

  const confirm = useCallback(async () => {
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
    }
  }, [onConfirm]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[100] grid place-items-center bg-black/50 p-4 backdrop-blur-sm"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget && !busy) setOpen(false);
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Confirm sign out"
        className="w-full max-w-sm rounded-xl border border-line bg-panel p-4 shadow-xl"
      >
        <p className="text-sm font-medium text-ink">{title}</p>
        <p className="mt-1 text-xs leading-relaxed text-ink-3">{body}</p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => setOpen(false)}
            className="rounded-md px-2.5 py-1.5 text-xs text-ink-2 ring-1 ring-inset ring-line transition hover:bg-panel-2 hover:text-ink disabled:opacity-50"
          >
            No, stay signed in
          </button>
          <button
            ref={confirmRef}
            type="button"
            disabled={busy}
            onClick={() => void confirm()}
            className="rounded-md bg-invert px-2.5 py-1.5 text-xs font-medium text-invert-ink transition hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "Signing out…" : "Yes, sign out"}
          </button>
        </div>
      </div>
    </div>
  );
}
