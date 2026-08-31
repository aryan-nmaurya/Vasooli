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
  ["/live", "Overview", "Queue and recovery health", "O"],
  ["/live/invoices", "Invoices", "Receivables and imports", "I"],
  ["/live/promises", "Promises", "Payment commitments", "P"],
  ["/live/disputes", "Disputes", "Human-review cases", "D"],
  ["/live/exceptions", "Exceptions", "Failures needing action", "!"],
  ["/live/audit", "Audit log", "Every recorded action", "A"],
] as const;

const LIVE_SETUP_NAV = [
  ["/live/integrations", "Integrations"],
  ["/live/settings", "Workspace settings"],
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
  return <div className="dashboard-shell">
    <aside className="dashboard-sidebar">
      <div className="flex h-16 items-center border-b border-line px-5"><Brand href="/live" subtitle="Live workspace" /></div>
      <div className="flex min-h-0 flex-1 flex-col px-3 py-5">
        <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">Merchant workspace</p>
        <nav aria-label="Live workspace" className="flex flex-col gap-1">
          {LIVE_NAV.map(([href, label, description, icon]) => {
            const active = href === "/live" ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);
            return <Link key={href} href={href} aria-current={active ? "page" : undefined} className={`group flex min-h-12 items-center gap-3 rounded-lg px-3 py-2 transition-colors ${active ? "bg-nav-active text-ink shadow-sm ring-1 ring-inset ring-line" : "text-ink-3 hover:bg-panel-2 hover:text-ink"}`}>
              <span aria-hidden className={`grid size-8 shrink-0 place-items-center rounded-md text-xs font-semibold ${active ? "bg-accent-soft text-accent" : "text-ink-4 ring-1 ring-inset ring-line group-hover:text-ink-2"}`}>{icon}</span>
              <span className="min-w-0"><span className="block text-sm font-medium leading-5">{label}</span><span className="block truncate text-[11px] leading-4 text-ink-4">{description}</span></span>
            </Link>;
          })}
        </nav>
        <div className="mt-auto space-y-1 pt-6">
          <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">Manage</p>
          {LIVE_SETUP_NAV.map(([href, label]) => <Link key={href} href={href} className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs transition ${pathname === href ? "bg-nav-active font-medium text-ink ring-1 ring-inset ring-line" : "text-ink-3 hover:bg-panel-2 hover:text-ink"}`}><span aria-hidden className="grid size-6 place-items-center rounded-md border border-line">{href.endsWith("settings") ? "⚙" : "↗"}</span>{label}</Link>)}
          <div className="mt-3 rounded-xl border border-line bg-panel-2/65 p-3"><div className="flex items-center gap-2"><span className="size-1.5 rounded-full bg-emerald-500" /><span className="text-xs font-medium text-ink">Live tenant</span></div><p className="mt-1.5 text-[11px] leading-4 text-ink-4">Scoped records and audited actions</p></div>
        </div>
      </div>
    </aside>
    <div className="dashboard-content">
      <header className="dashboard-topbar"><div className="lg:hidden"><Brand href="/live" compact /></div><div className="ml-auto"><LiveSignOutButton /></div></header>
      <main className="w-full flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">{children}</main>
      <footer className="border-t border-line px-4 py-4 text-[11px] text-ink-4 sm:px-6 lg:px-8"><div className="flex flex-wrap items-center gap-x-4 gap-y-1"><span>Vasooli · Live merchant workspace</span><span className="ml-auto">Tenant-scoped · Policy-controlled · Audited</span></div></footer>
      <nav aria-label="Live workspace mobile" className="dashboard-mobile-nav scroll-x flex gap-1 lg:hidden">{LIVE_NAV.slice(0, 5).map(([href, label, , icon]) => <Link key={href} href={href} className={`flex min-w-[64px] flex-col items-center gap-0.5 rounded-lg px-2 py-1.5 text-[10px] font-medium ${pathname === href ? "text-accent" : "text-ink-3"}`}><span className="grid size-6 place-items-center">{icon}</span><span>{label}</span></Link>)}</nav>
    </div>
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
