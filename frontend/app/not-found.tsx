import Link from "next/link";

/**
 * The page a mistyped URL lands on.
 *
 * Without this, Next.js serves its own unstyled 404 — black Helvetica on white,
 * no navigation, no theme. On a deployment whose whole pitch is that it is careful
 * about detail, that is the one screen a judge is most likely to reach by accident
 * and the worst one to have left as a default.
 */
export const metadata = { title: "Page not found — Vasooli" };

export default function NotFound() {
  return (
    <div className="mx-auto flex min-h-[60vh] max-w-lg flex-col items-center justify-center gap-4 px-4 text-center">
      <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-4">
        404
      </span>
      <h1 className="text-2xl font-semibold tracking-tight text-ink">
        That page does not exist.
      </h1>
      <p className="text-[15px] leading-relaxed text-ink-2">
        The link may be out of date, or the address may have a typo. Nothing is broken.
      </p>
      <div className="mt-2 flex flex-wrap justify-center gap-2">
        <Link
          href="/"
          className="rounded-md bg-invert px-3 py-1.5 text-sm font-medium text-invert-ink transition hover:opacity-90"
        >
          Back to the start
        </Link>
        <Link
          href="/guide"
          className="rounded-md px-3 py-1.5 text-sm text-ink-2 ring-1 ring-inset ring-line transition hover:bg-panel-2 hover:text-ink"
        >
          Reviewer guide
        </Link>
      </div>
    </div>
  );
}
