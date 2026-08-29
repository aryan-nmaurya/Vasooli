import type { Metadata } from "next";
import Link from "next/link";

import { Nav } from "@/components/Nav";
import { RuntimeBanner } from "@/components/RuntimeBanner";
import { SignOutButton } from "@/components/SignOutButton";
import { ThemeToggle } from "@/components/ThemeToggle";
import { currentSession } from "@/lib/session";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vasooli — AI Receivables Recovery",
  description: "Chase. Track. Reconcile. Recover.",
};

/**
 * Applied before first paint, so the page never flashes the wrong theme while React
 * hydrates. Falls back to the operating system preference on a first visit.
 */
const THEME_SCRIPT = `
(function(){
  try {
    var saved = localStorage.getItem('vasooli-theme');
    var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.setAttribute('data-theme', saved || (prefersDark ? 'dark' : 'light'));
  } catch (e) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();
`;

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Dashboard navigation and the runtime banner are meaningless to an anonymous
  // visitor: every link behind them redirects to /login, so showing them turns the
  // landing page into four dead ends.
  const signedIn = Boolean(await currentSession());

  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="flex min-h-screen flex-col bg-surface antialiased">
        <header
          className={
            signedIn
              ? "sticky top-0 z-10 border-b border-line bg-surface/85 backdrop-blur"
              : "landing-site-header sticky top-0 z-40 border-b backdrop-blur"
          }
        >
          <div className="mx-auto flex max-w-[1200px] items-center gap-2 px-3 py-3.5 sm:gap-4 sm:px-6">
            <Link
              href="/"
              className="text-[15px] font-semibold tracking-tight text-ink hover:opacity-80"
            >
              <span className="hidden min-[360px]:inline">Vasooli</span>
              <span className="min-[360px]:hidden" aria-hidden>
                V
              </span>
              <span className="sr-only min-[360px]:hidden">Vasooli</span>
            </Link>

            {signedIn ? (
              <>
                {/* Divider, so the wordmark reads as a brand rather than a first menu item. */}
                <span aria-hidden className="h-5 w-px bg-line" />
                <Nav />
              </>
            ) : (
              <nav className="landing-site-nav" aria-label="Landing page">
                <Link href="/#how">How it works</Link>
                <Link href="/#safety">Safety</Link>
                <Link href="/#proof">Proof</Link>
              </nav>
            )}

            <div className="ml-auto flex items-center gap-1.5">
              {/* De-emphasised: a tagline, not navigation. */}
              <span className="mr-1 hidden text-xs text-ink-4 md:inline">
                Chase. Track. Reconcile. Recover.
              </span>
              <SignOutButton signedIn={signedIn} />
              <span className="anonymous-theme-toggle contents">
                <ThemeToggle />
              </span>
              {!signedIn ? (
                <>
                  <a
                    href="https://github.com/aryan-nmaurya/Vasooli"
                    target="_blank"
                    rel="noreferrer"
                    className="landing-github-cta"
                  >
                    GitHub <span aria-hidden>↗</span>
                  </a>
                  <Link href="/login" className="landing-header-cta">
                    Open demo <span aria-hidden>↗</span>
                  </Link>
                </>
              ) : null}
            </div>
          </div>
        </header>
        {signedIn ? <RuntimeBanner /> : null}
        <main className={signedIn ? "mx-auto w-full max-w-[1200px] flex-1 px-3 py-7 sm:px-6" : "w-full flex-1"}>
          {children}
        </main>
        {/* The evidence for this system's central claim lives in the test suite, not
            the interface. Without a link out, a reviewer browsing the dashboard alone
            never learns it exists. */}
        <footer className={signedIn ? "mt-8 border-t border-line" : "landing-site-footer border-t"}>
          <div className="mx-auto flex max-w-[1200px] flex-wrap items-center gap-x-4 gap-y-2 px-3 py-5 text-xs text-ink-4 sm:px-6">
            <span>Vasooli — AI receivables recovery</span>
            <Link href="/guide" className="text-ink-3 hover:text-ink">
              Reviewer guide
            </Link>
            <a
              href="https://github.com/aryan-nmaurya/Vasooli"
              target="_blank"
              rel="noreferrer"
              className="text-ink-3 hover:text-ink"
            >
              Source and tests
            </a>
            <span className="ml-auto">Single merchant · Razorpay test mode</span>
          </div>
        </footer>
      </body>
    </html>
  );
}
