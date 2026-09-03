"use client";

import Link from "next/link";
import { useEffect } from "react";

/**
 * The boundary for a render error inside a route.
 *
 * `reset()` re-renders the segment, which is genuinely useful here: most failures in
 * this app are a fetch that did not come back, not corrupt state, so trying again
 * often works. The error message itself is NOT shown — it can carry request URLs and
 * server details, and a viewer can do nothing with it. It goes to the console for
 * whoever is debugging, and the screen stays calm.
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Route error:", error);
  }, [error]);

  return (
    <div className="mx-auto flex min-h-[60vh] max-w-lg flex-col items-center justify-center gap-4 px-4 text-center">
      <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-4">
        Something went wrong
      </span>
      <h1 className="text-2xl font-semibold tracking-tight text-ink">
        This page could not be loaded.
      </h1>
      <p className="text-[15px] leading-relaxed text-ink-2">
        Your data is unaffected — this is a display problem, not a change to the ledger.
        Trying again usually works.
      </p>
      {error.digest ? (
        <p className="font-mono text-[11px] text-ink-4">Reference: {error.digest}</p>
      ) : null}
      <div className="mt-2 flex flex-wrap justify-center gap-2">
        <button
          onClick={reset}
          className="rounded-md bg-invert px-3 py-1.5 text-sm font-medium text-invert-ink transition hover:opacity-90"
        >
          Try again
        </button>
        <Link
          href="/"
          className="rounded-md px-3 py-1.5 text-sm text-ink-2 ring-1 ring-inset ring-line transition hover:bg-panel-2 hover:text-ink"
        >
          Back to the start
        </Link>
      </div>
    </div>
  );
}
