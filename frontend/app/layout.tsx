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
  const signedIn = Boolean(await currentSession());

  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-screen bg-surface text-ink antialiased">
        {signedIn ? (
          <DashboardShell>{children}</DashboardShell>
        ) : (
          <PublicShell>{children}</PublicShell>
        )}
      </body>
    </html>
  );
}

function DashboardShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="dashboard-shell">
      <aside className="dashboard-sidebar">
        <div className="flex h-16 items-center border-b border-line px-5">
          <Brand />
        </div>
        <div className="flex min-h-0 flex-1 flex-col px-3 py-5">
          <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">
            Workspace
          </p>
          <Nav />
          <div className="mt-auto space-y-3 pt-6">
            <SettingsLink />
            <Link href="/guide" className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs text-ink-3 transition hover:bg-panel-2 hover:text-ink">
              <span aria-hidden className="grid size-6 place-items-center rounded-md border border-line text-[11px] font-semibold">?</span>
              Reviewer guide
            </Link>
            <div className="rounded-xl border border-line bg-panel-2/65 p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-ink">Razorpay</span>
                <span className="rounded-full bg-amber-500/12 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-300">Test mode</span>
              </div>
              <p className="mt-1.5 text-[11px] leading-4 text-ink-4">Single merchant workspace</p>
            </div>
          </div>
        </div>
      </aside>

      <div className="dashboard-content">
        <header className="dashboard-topbar">
          <div className="flex min-w-0 items-center gap-3">
            <div className="lg:hidden"><Brand compact /></div>
            <span className="hidden h-5 w-px bg-line sm:block lg:hidden" aria-hidden />
            <div className="hidden min-w-0 sm:block">
              <p className="truncate text-xs font-medium text-ink-2">Receivables recovery</p>
              <p className="truncate text-[10px] text-ink-4">Live operations workspace</p>
            </div>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <SettingsLink compact />
            <ThemeToggle />
            <SignOutButton signedIn />
          </div>
        </header>
        <RuntimeBanner />
        <main className="w-full flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">{children}</main>
        <footer className="border-t border-line px-4 py-4 text-[11px] text-ink-4 sm:px-6 lg:px-8">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span>Vasooli · AI receivables recovery</span>
            <span className="ml-auto">Razorpay test mode</span>
          </div>
        </footer>
        <div className="dashboard-mobile-nav lg:hidden"><Nav variant="mobile" /></div>
      </div>
    </div>
  );
}

function SettingsLink({ compact = false }: { compact?: boolean }) {
  return (
    <Link
      href="/settings"
      aria-label={compact ? "Workspace settings" : undefined}
      title={compact ? "Workspace settings" : undefined}
      className={compact
        ? "grid size-9 place-items-center rounded-lg border border-line bg-panel text-ink-3 transition hover:bg-panel-2 hover:text-ink lg:hidden"
        : "flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs text-ink-3 transition hover:bg-panel-2 hover:text-ink"}
    >
      <span className={compact ? "grid place-items-center" : "grid size-6 shrink-0 place-items-center rounded-md border border-line"}>
        <svg aria-hidden width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.86 2.86-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1v.1H9.5V21a1.7 1.7 0 0 0-.4-1 1.7 1.7 0 0 0-1-.6 1.7 1.7 0 0 0-1.88.34l-.06.06-2.86-2.86.06-.06A1.7 1.7 0 0 0 3.7 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1-.4H2V9.5h.1a1.7 1.7 0 0 0 1-.4 1.7 1.7 0 0 0 .6-1 1.7 1.7 0 0 0-.34-1.88l-.06-.06L6.16 3.3l.06.06A1.7 1.7 0 0 0 8.1 3.7a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .4-1V2h4.1v.1a1.7 1.7 0 0 0 .4 1 1.7 1.7 0 0 0 1 .6 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.86 2.86-.06.06A1.7 1.7 0 0 0 19.4 8.1c.1.38.3.72.6 1 .28.25.63.4 1 .4h.1v4.1H21c-.37 0-.72.15-1 .4-.3.28-.5.62-.6 1Z" />
        </svg>
      </span>
      {!compact ? <span>Workspace settings</span> : null}
    </Link>
  );
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link href="/" className="flex items-center gap-2.5 text-ink hover:opacity-80">
      <span className="grid size-8 place-items-center rounded-lg bg-accent text-sm font-bold text-white shadow-sm">V</span>
      <span>
        <span className="block text-sm font-semibold tracking-tight">Vasooli</span>
        {!compact ? <span className="block text-[10px] uppercase tracking-[0.16em] text-ink-4">Recovery desk</span> : null}
      </span>
    </Link>
  );
}

function PublicShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="landing-site-header sticky top-0 z-40 border-b backdrop-blur">
        <div className="mx-auto flex max-w-[1200px] items-center gap-2 px-3 py-3.5 sm:gap-4 sm:px-6">
          <Link href="/" className="text-[15px] font-semibold tracking-tight hover:opacity-80">Vasooli</Link>
          <nav className="landing-site-nav" aria-label="Landing page">
            <Link href="/#how">How it works</Link>
            <Link href="/#safety">Safety</Link>
            <Link href="/#proof">Proof</Link>
          </nav>
          <div className="ml-auto flex items-center gap-1.5">
            <span className="anonymous-theme-toggle contents"><ThemeToggle /></span>
            <a href="https://github.com/aryan-nmaurya/Vasooli" target="_blank" rel="noreferrer" className="landing-github-cta">GitHub <span aria-hidden>↗</span></a>
            <Link href="/login" className="landing-header-cta">Open demo <span aria-hidden>↗</span></Link>
          </div>
        </div>
      </header>
      <main className="w-full flex-1">{children}</main>
      <footer className="landing-site-footer border-t">
        <div className="mx-auto flex max-w-[1200px] flex-wrap items-center gap-x-4 gap-y-2 px-3 py-5 text-xs text-ink-4 sm:px-6">
          <span>Vasooli — AI receivables recovery</span>
          <Link href="/guide" className="text-ink-3 hover:text-ink">Reviewer guide</Link>
          <a href="https://github.com/aryan-nmaurya/Vasooli" target="_blank" rel="noreferrer" className="text-ink-3 hover:text-ink">Source and tests</a>
          <span className="ml-auto">Single merchant · Razorpay test mode</span>
        </div>
      </footer>
    </div>
  );
}
