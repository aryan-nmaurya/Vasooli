import Link from "next/link";

/**
 * What a signed-out visitor sees on a live workspace page.
 *
 * Every live page previously rendered the sentence "Sign in to a live workspace
 * first." and stopped there — no link, and on the invoices page a fully-drawn import
 * form above it that could only fail. A dead end that names the problem and offers no
 * way out reads as broken software, and it is the first screen anyone reaching a live
 * URL without a session actually sees.
 */
export function LiveSignInPrompt({ what }: { what: string }) {
  return (
    <div className="mt-8 rounded-2xl border border-line bg-panel p-6 text-center">
      <h2 className="text-base font-semibold">Sign in to continue</h2>
      <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-ink-3">
        {what} belongs to your live workspace, so it loads once you are signed in.
      </p>
      <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
        <Link
          href="/live/login"
          className="inline-flex min-h-11 items-center justify-center rounded-lg bg-accent px-5 text-sm font-semibold text-white"
        >
          Sign in
        </Link>
        <Link
          href="/register"
          className="inline-flex min-h-11 items-center justify-center rounded-lg border border-line px-5 text-sm font-medium text-ink-2 hover:text-ink"
        >
          Create a workspace
        </Link>
      </div>
    </div>
  );
}
