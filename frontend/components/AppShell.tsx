"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useEffect, useLayoutEffect, useState } from "react";

import { LiveSignOutButton } from "@/components/LiveSignOutButton";
import { Nav, WorkspaceNavIcon, type WorkspaceIconName } from "@/components/Nav";
import { RuntimeBanner } from "@/components/RuntimeBanner";
import { SignOutButton } from "@/components/SignOutButton";
import { ThemeToggle } from "@/components/ThemeToggle";
import { DemoExitGuard } from "@/components/DemoExitGuard";
import { LiveExitGuard } from "@/components/LiveExitGuard";
import { PaymentGate } from "@/components/PaymentGate";
import { SubscriptionBanner } from "@/components/SubscriptionBanner";
import { liveGet } from "@/lib/live-api";

const DEMO_ROUTES = ["/recovered", "/promises", "/audit", "/invoices", "/settings"];
const LIVE_CORE_NAV = [
  ["/live", "Overview", "Queue and recovery health", "O"],
  ["/live/recovered", "Recovered", "Settled invoices", "R"],
  ["/live/promises", "Promises", "Payment commitments", "P"],
  ["/live/audit", "Audit log", "Every automated action", "A"],
] as const;

const LIVE_OPERATIONS_NAV = [
  ["/live/invoices", "Invoices", "Receivables and imports", "I"],
  ["/live/disputes", "Disputes", "Human-review cases", "D"],
  ["/live/exceptions", "Exceptions", "Failures needing action", "!"],
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
    if (shell === "public") {
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
    <DemoExitGuard />
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
  return <div className="dashboard-shell live-dashboard-shell">
    <LiveExitGuard />
    <PaymentGate />
    <aside className="dashboard-sidebar">
      <div className="flex h-16 items-center border-b border-line px-5"><Brand href="/live" subtitle="Recovery desk" /></div>
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3 py-5">
        <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">Live workspace</p>
        <LiveNavGroup items={LIVE_CORE_NAV} pathname={pathname} ariaLabel="Live recovery workspace" />
        <div className="mt-5 border-t border-line-2 pt-4">
          <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">Live operations</p>
          <LiveNavGroup items={LIVE_OPERATIONS_NAV} pathname={pathname} ariaLabel="Live operations" />
        </div>
        <div className="mt-auto space-y-1 pt-6">
          <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">Manage</p>
          <Link href="/live/settings" className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs transition ${pathname === "/live/settings" || pathname.startsWith("/live/settings/") ? "bg-nav-active font-medium text-ink ring-1 ring-inset ring-line" : "text-ink-3 hover:bg-panel-2 hover:text-ink"}`}><span aria-hidden className="grid size-6 place-items-center rounded-md border border-line"><WorkspaceNavIcon name="settings" /></span>Settings</Link>
          <LiveWorkspaceIdentity />
        </div>
      </div>
    </aside>
    <div className="dashboard-content">
      <header className="dashboard-topbar"><div className="lg:hidden"><Brand href="/live" compact /></div><div className="ml-auto flex items-center gap-2"><details className="group relative lg:hidden"><summary aria-label="Open live workspace menu" className="grid size-9 cursor-pointer list-none place-items-center rounded-lg border border-line bg-panel text-ink-3"><WorkspaceNavIcon name="settings" /></summary><div className="absolute right-0 top-11 z-50 w-64 rounded-xl border border-line bg-panel p-2 shadow-xl"><p className="px-2 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">Live operations</p>{LIVE_OPERATIONS_NAV.map(([href, label]) => <Link key={href} href={href} className="flex items-center gap-2.5 rounded-lg px-2 py-2 text-xs text-ink-2 hover:bg-panel-2"><WorkspaceNavIcon name={liveIconFor(href)} />{label}</Link>)}<div className="my-1 border-t border-line-2" /><Link href="/live/settings" className="flex items-center gap-2.5 rounded-lg px-2 py-2 text-xs font-medium text-ink hover:bg-panel-2"><WorkspaceNavIcon name="settings" />Settings</Link></div></details><ThemeToggle /><LiveSignOutButton /></div></header>
      <SubscriptionBanner />
      <main className="w-full flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">{children}</main>
      <footer className="border-t border-line px-4 py-4 text-[11px] text-ink-4 sm:px-6 lg:px-8"><div className="flex flex-wrap items-center gap-x-4 gap-y-1"><span>Vasooli · Live merchant workspace</span><span className="ml-auto">Tenant-scoped · Policy-controlled · Audited</span></div></footer>
      <nav aria-label="Live workspace mobile" className="dashboard-mobile-nav grid grid-cols-4 border-t border-line bg-panel/95 px-1 pb-[max(0.35rem,env(safe-area-inset-bottom))] pt-1 backdrop-blur lg:hidden">{LIVE_CORE_NAV.map(([href, label]) => { const active = href === "/live" ? pathname === href : pathname === href || pathname.startsWith(`${href}/`); return <Link key={href} href={href} className={`flex min-w-0 flex-col items-center gap-0.5 rounded-lg px-1 py-1.5 text-[10px] font-medium transition-colors ${active ? "text-accent" : "text-ink-3 hover:bg-panel-2 hover:text-ink"}`}><span className="grid size-6 place-items-center"><WorkspaceNavIcon name={liveIconFor(href)} /></span><span className="truncate">{label === "Audit log" ? "Audit" : label}</span></Link>; })}</nav>
    </div>
  </div>;
}

type LiveWorkspaceProfile = {
  business_name: string;
  subscription: { label: string; slug: string; status: string; trial_ends_at: string | null };
};

function LiveWorkspaceIdentity() {
  const [profile, setProfile] = useState<LiveWorkspaceProfile | null>(null);

  useEffect(() => {
    const merchantId = window.localStorage.getItem("vasooli_live_merchant");
    if (!merchantId) return;
    let active = true;
    liveGet<LiveWorkspaceProfile>("/api/live/workspace/profile", merchantId)
      .then((result) => { if (active) setProfile(result); })
      .catch(() => { /* Keep a neutral workspace fallback if profile loading fails. */ });
    return () => { active = false; };
  }, []);

  const subscription = profile?.subscription;
  const planLabel = subscription?.label || "Plan unavailable";
  const planTone = subscription?.slug === "trial"
    ? "bg-amber-500/12 text-amber-700 dark:text-amber-300"
    : "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300";

  return (
    <div className="mt-3 rounded-xl border border-line bg-panel-2/65 p-3">
      <div className="flex items-start justify-between gap-2">
        <span className="min-w-0 truncate text-xs font-semibold text-ink" title={profile?.business_name}>
          {profile?.business_name || "Live workspace"}
        </span>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider ${planTone}`}>
          {planLabel}
        </span>
      </div>
      <p className="mt-1.5 text-[11px] leading-4 text-ink-4">
        {subscription?.slug === "trial" ? "Starter plan trial · Live mode" : "Active subscription · Live mode"}
      </p>
    </div>
  );
}

type LiveNavItem = readonly [string, string, string, string];

function liveIconFor(href: string): WorkspaceIconName {
  if (href === "/live") return "overview";
  if (href.includes("recovered")) return "recovered";
  if (href.includes("promises")) return "promises";
  if (href.includes("audit")) return "audit";
  if (href.includes("invoices")) return "invoices";
  if (href.includes("disputes")) return "disputes";
  return "exceptions";
}

function LiveNavGroup({ items, pathname, ariaLabel }: { items: readonly LiveNavItem[]; pathname: string; ariaLabel: string }) {
  return <nav aria-label={ariaLabel} className="flex flex-col gap-1">{items.map(([href, label, description]) => {
    const active = href === "/live" ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);
    return <Link key={href} href={href} aria-current={active ? "page" : undefined} className={`group flex min-h-12 items-center gap-3 rounded-lg px-3 py-2 transition-colors ${active ? "bg-nav-active text-ink shadow-sm ring-1 ring-inset ring-line" : "text-ink-3 hover:bg-panel-2 hover:text-ink"}`}><span aria-hidden className={`grid size-8 shrink-0 place-items-center rounded-md transition-colors ${active ? "bg-accent-soft text-accent" : "text-ink-4 group-hover:text-ink-2"}`}><WorkspaceNavIcon name={liveIconFor(href)} /></span><span className="min-w-0"><span className="block text-sm font-medium leading-5">{label}</span><span className="block truncate text-[11px] leading-4 text-ink-4">{description}</span></span></Link>;
  })}</nav>;
}

function PublicShell({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-screen flex-col">
    <header className="landing-site-header sticky top-0 z-40 border-b backdrop-blur"><div className="mx-auto flex max-w-[1200px] items-center gap-2 px-3 py-3.5 sm:gap-4 sm:px-6">
      <Link href="/" className="text-[15px] font-semibold tracking-tight hover:opacity-80">Vasooli</Link>
      <nav className="landing-site-nav" aria-label="Landing page"><Link href="/#how">How it works</Link><Link href="/#integrations">Integrations</Link><Link href="/#safety">Safety</Link><Link href="/#operations">Operations</Link><Link href="/pricing">Pricing</Link></nav>
      <div className="ml-auto flex items-center"><Link href="/live/login" className="landing-header-signin">Sign in</Link><Link href="/register" className="landing-header-cta">Get started <span aria-hidden>↗</span></Link></div>
    </div></header>
    <main className="w-full flex-1">{children}</main>
    <footer className="landing-site-footer border-t">
      <div className="public-footer">
        <div className="public-footer-lead"><Brand href="/" subtitle="Recovery desk" /><p>Recover receivables with policy-controlled automation, trusted payment data, and a complete audit trail.</p></div>
        <div><strong>Product</strong><Link href="/#how">How it works</Link><Link href="/#integrations">Integrations</Link><Link href="/pricing">Pricing</Link><Link href="/login">Product demo</Link></div>
        <div><strong>Company</strong><Link href="/register">Create workspace</Link><Link href="/live/login">Sign in</Link><a href="mailto:hello@vasooli.space">Contact</a><a href="mailto:support@vasooli.space">Support</a></div>
        <div><strong>Legal</strong><Link href="/terms">Terms of Service</Link><Link href="/privacy">Privacy Policy</Link><Link href="/dpa">Data Processing Addendum</Link><a href="mailto:security@vasooli.space">Report a security issue</a></div>
      </div>
      <div className="public-footer-bottom"><span>© {new Date().getFullYear()} Vasooli. All rights reserved.</span><span>Policy-controlled · Auditable · Tenant-scoped</span></div>
    </footer>
  </div>;
}

function SettingsLink({ compact = false }: { compact?: boolean }) {
  return <Link href="/settings" aria-label={compact ? "Workspace settings" : undefined} className={compact ? "grid size-9 place-items-center rounded-lg border border-line bg-panel text-ink-3 lg:hidden" : "flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs text-ink-3 transition hover:bg-panel-2 hover:text-ink"}><span aria-hidden className="grid size-6 place-items-center rounded-md border border-line">⚙</span>{compact ? null : <span>Workspace settings</span>}</Link>;
}

function Brand({ href, subtitle, compact = false }: { href: string; subtitle?: string; compact?: boolean }) {
  return <Link href={href} className="flex shrink-0 items-center gap-2.5 text-ink hover:opacity-80"><Image src="/vasooli-logo.png" alt="" width={32} height={32} priority className="size-8 shrink-0 rounded-lg" /><span><span className="block text-sm font-semibold tracking-tight">Vasooli</span>{!compact && subtitle ? <span className="block text-[10px] uppercase tracking-[0.16em] text-ink-4">{subtitle}</span> : null}</span></Link>;
}
