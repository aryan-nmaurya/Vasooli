import type { Metadata } from "next";
import Link from "next/link";

import { Nav } from "@/components/Nav";
import { RuntimeBanner } from "@/components/RuntimeBanner";
import { ThemeToggle } from "@/components/ThemeToggle";
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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-screen bg-surface antialiased">
        <header className="sticky top-0 z-10 border-b border-line bg-surface/85 backdrop-blur">
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

            {/* Divider, so the wordmark reads as a brand rather than a first menu item. */}
            <span aria-hidden className="h-5 w-px bg-line" />

            <Nav />

            {/* Pushed right and de-emphasised: a tagline, not navigation. */}
            <span className="ml-auto hidden text-xs text-ink-4 md:inline">
              Chase. Track. Reconcile. Recover.
            </span>

            <ThemeToggle />
          </div>
        </header>
        <RuntimeBanner />
        <main className="mx-auto max-w-[1200px] px-3 py-7 sm:px-6">{children}</main>
      </body>
    </html>
  );
}
