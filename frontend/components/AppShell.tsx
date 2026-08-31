"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useLayoutEffect } from "react";

import { LiveSignOutButton } from "@/components/LiveSignOutButton";
import { Nav } from "@/components/Nav";
import { RuntimeBanner } from "@/components/RuntimeBanner";
import { SignOutButton } from "@/components/SignOutButton";
import { ThemeToggle } from "@/components/ThemeToggle";

const DEMO_ROUTES = ["/recovered", "/promises", "/audit", "/invoices", "/settings"];
const LIVE_NAV = [
  ["/live", "Overview"],
  ["/live/invoices", "Queue"],
  ["/live/promises", "Promises"],
  ["/live/disputes", "Disputes"],
  ["/live/exceptions", "Exceptions"],
  ["/live/audit", "Audit"],
  ["/live/integrations", "Integrations"],
  ["/live/settings", "Settings"],
] as const;

export function shellForPath(pathname: string, guidedSignedIn: boolean) {
  pathname = pathname === "/" ? pathname : pathname.replace(/\/+$/, "");
  if (pathname === "/live" || (pathname.startsWith("/live/") && pathname !== "/live/login")) return "live";
  if (guidedSignedIn && (pathname === "/" || DEMO_ROUTES.some((route) => pathname === route || pathname.startsWith(`${route}/`)))) return "demo";
  return "public";
}

export function AppShell({ children, guidedSignedIn }: { children: React.ReactNode; guidedSignedIn: boolean }) {
  const pathname = usePathname();
  const shell = shellForPath(pathname, guidedSignedIn);

  useLayoutEffect(() => {
    if (shell !== "demo") {
      document.documentElement.setAttribute("data-theme", "dark");
      return;
    }
    let theme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    try { theme = window.localStorage.getItem("vasooli-theme") || theme; } catch { /* Use the OS preference when storage is unavailable. */ }
    document.documentElement.setAttribute("data-theme", theme);
  }, [shell]);

  if (shell === "live") return <LiveShell pathname={pathname}>{children}</LiveShell>;
  if (shell === "demo") return <DashboardShell>{children}</DashboardShell>;
  return <PublicShell>{children}</PublicShell>;
}

function DashboardShell({ children }: { children: React.ReactNode }) {
  return <div className="dashboard-shell">
    <aside className="dashboard-sidebar">
      <div className="flex h-16 items-center border-b border-line px-5"><Brand href="/" subtitle="Recovery desk" /></div>
      <div className="flex min-h-0 flex-1 flex-col px-3 py-5">
        <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">Demo workspace</p>
        <Nav />
        <div className="mt-auto space-y-3 pt-6">
          <SettingsLink />
          <Link href="/guide" className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs text-ink-3 transition hover:bg-panel-2 hover:text-ink"><span aria-hidden className="grid size-6 place-items-center rounded-md border border-line text-[11px] font-semibold">?</span>Reviewer guide</Link>
          <div className="rounded-xl border border-line bg-panel-2/65 p-3"><div className="flex items-center justify-between gap-2"><span className="text-xs font-medium text-ink">Razorpay</span><span className="rounded-full bg-amber-500/12 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-300">Test mode</span></div><p className="mt-1.5 text-[11px] leading-4 text-ink-4">Guided single-merchant demo</p></div>
        </div>
      </div>
    </aside>
    <div className="dashboard-content">
      <header className="dashboard-topbar"><div className="lg:hidden"><Brand href="/" compact /></div><div className="ml-auto flex items-center gap-2"><SettingsLink compact /><ThemeToggle /><SignOutButton signedIn /></div></header>
      <RuntimeBanner />
      <main className="w-full flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">{children}</main>
      <footer className="border-t border-line px-4 py-4 text-[11px] text-ink-4 sm:px-6 lg:px-8"><div className="flex flex-wrap items-center gap-x-4 gap-y-1"><span>Vasooli · Guided product demo</span><span className="ml-auto">No live customer contact</span></div></footer>
      <div className="dashboard-mobile-nav lg:hidden"><Nav variant="mobile" /></div>
    </div>
  </div>;
}

function LiveShell({ children, pathname }: { children: React.ReactNode; pathname: string }) {
  return <div className="flex min-h-screen flex-col bg-surface">
    <header className="sticky top-0 z-40 border-b border-line bg-surface/95 backdrop-blur">
      <div className="mx-auto flex max-w-[1440px] items-center gap-4 px-4 py-3 sm:px-6">
        <Brand href="/live" subtitle="Live workspace" />
        <nav aria-label="Live workspace" className="scroll-x ml-2 hidden flex-1 items-center gap-1 lg:flex">
          {LIVE_NAV.map(([href, label]) => <Link key={href} href={href} aria-current={pathname === href ? "page" : undefined} className={`whitespace-nowrap rounded-lg px-3 py-2 text-xs transition ${pathname === href ? "bg-accent/12 font-semibold text-accent" : "text-ink-3 hover:bg-panel hover:text-ink"}`}>{label}</Link>)}
        </nav>
        <div className="ml-auto flex items-center gap-2"><LiveSignOutButton /></div>
      </div>
      <nav aria-label="Live workspace mobile" className="scroll-x flex gap-1 border-t border-line px-3 py-2 lg:hidden">
        {LIVE_NAV.map(([href, label]) => <Link key={href} href={href} aria-current={pathname === href ? "page" : undefined} className={`whitespace-nowrap rounded-md px-3 py-1.5 text-xs ${pathname === href ? "bg-accent/12 font-semibold text-accent" : "text-ink-3"}`}>{label}</Link>)}
      </nav>
    </header>
    <div className="flex-1">{children}</div>
    <footer className="border-t border-line px-4 py-4 text-xs text-ink-4 sm:px-6"><div className="mx-auto flex max-w-[1440px] flex-wrap gap-3"><span>Vasooli live workspace</span><span className="ml-auto">Tenant-scoped · Policy-controlled · Audited</span></div></footer>
  </div>;
}

function PublicShell({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-screen flex-col">
    <header className="landing-site-header sticky top-0 z-40 border-b backdrop-blur"><div className="mx-auto flex max-w-[1200px] items-center gap-2 px-3 py-3.5 sm:gap-4 sm:px-6">
      <Link href="/" className="text-[15px] font-semibold tracking-tight hover:opacity-80">Vasooli</Link>
      <nav className="landing-site-nav" aria-label="Landing page"><Link href="/#how">How it works</Link><Link href="/#integrations">Integrations</Link><Link href="/#safety">Safety</Link><Link href="/#operations">Operations</Link><Link href="/pricing">Pricing</Link></nav>
      <div className="ml-auto flex items-center gap-1.5"><Link href="/live/login" className="hidden text-xs text-ink-3 hover:text-ink sm:inline">Sign in</Link><Link href="/register" className="landing-header-cta">Get started <span aria-hidden>↗</span></Link></div>
    </div></header>
    <main className="w-full flex-1">{children}</main>
    <footer className="landing-site-footer border-t"><div className="mx-auto flex max-w-[1200px] flex-wrap items-center gap-x-4 gap-y-2 px-3 py-5 text-xs text-ink-4 sm:px-6"><span>Vasooli — AI receivables recovery</span><Link href="/pricing">Pricing</Link><Link href="/terms">Terms</Link><Link href="/privacy">Privacy</Link><Link href="/dpa">DPA</Link><Link href="/live/login">Sign in</Link><span className="ml-auto">Policy-controlled · Auditable · Tenant-scoped</span></div></footer>
  </div>;
}

function SettingsLink({ compact = false }: { compact?: boolean }) {
  return <Link href="/settings" aria-label={compact ? "Workspace settings" : undefined} className={compact ? "grid size-9 place-items-center rounded-lg border border-line bg-panel text-ink-3 lg:hidden" : "flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs text-ink-3 transition hover:bg-panel-2 hover:text-ink"}><span aria-hidden className="grid size-6 place-items-center rounded-md border border-line">⚙</span>{compact ? null : <span>Workspace settings</span>}</Link>;
}

function Brand({ href, subtitle, compact = false }: { href: string; subtitle?: string; compact?: boolean }) {
  return <Link href={href} className="flex shrink-0 items-center gap-2.5 text-ink hover:opacity-80"><span className="grid size-8 place-items-center rounded-lg bg-accent text-sm font-bold text-white shadow-sm">V</span><span><span className="block text-sm font-semibold tracking-tight">Vasooli</span>{!compact && subtitle ? <span className="block text-[10px] uppercase tracking-[0.16em] text-ink-4">{subtitle}</span> : null}</span></Link>;
}
