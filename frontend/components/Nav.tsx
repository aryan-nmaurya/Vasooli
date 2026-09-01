"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export type WorkspaceIconName = "overview" | "recovered" | "promises" | "audit" | "invoices" | "disputes" | "exceptions" | "integrations" | "settings" | "billing" | "team" | "policy";

const NAV: Array<{ href: string; label: string; description: string; icon: WorkspaceIconName }> = [
  { href: "/", label: "Overview", description: "Queue and recovery health", icon: "overview" },
  { href: "/recovered", label: "Recovered", description: "Settled invoices", icon: "recovered" },
  { href: "/promises", label: "Promises", description: "Payment commitments", icon: "promises" },
  { href: "/audit", label: "Audit log", description: "Every automated action", icon: "audit" },
];

export function Nav({ variant = "sidebar" }: { variant?: "sidebar" | "mobile" }) {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Dashboard"
      className={variant === "sidebar" ? "flex flex-col gap-1" : "grid grid-cols-4 border-t border-line bg-panel/95 px-1 pb-[max(0.35rem,env(safe-area-inset-bottom))] pt-1 backdrop-blur"}
    >
      {NAV.map((item) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={variant === "sidebar"
              ? `group flex min-h-12 items-center gap-3 rounded-lg px-3 py-2 transition-colors ${active ? "bg-nav-active text-ink shadow-sm ring-1 ring-inset ring-line" : "text-ink-3 hover:bg-panel-2 hover:text-ink"}`
              : `flex min-w-0 flex-col items-center gap-0.5 rounded-lg px-1 py-1.5 text-[10px] font-medium transition-colors ${active ? "text-accent" : "text-ink-3 hover:bg-panel-2 hover:text-ink"}`}
          >
            <span aria-hidden className={variant === "sidebar"
              ? `grid size-8 shrink-0 place-items-center rounded-md transition-colors ${active ? "bg-accent-soft text-accent" : "text-ink-4 group-hover:text-ink-2"}`
              : "grid size-6 place-items-center"}
            >
              <WorkspaceNavIcon name={item.icon} />
            </span>
            {variant === "sidebar" ? (
              <span className="min-w-0">
                <span className="block text-sm font-medium leading-5">{item.label}</span>
                <span className="block truncate text-[11px] leading-4 text-ink-4">{item.description}</span>
              </span>
            ) : (
              <span className="truncate">{item.label === "Audit log" ? "Audit" : item.label}</span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}

export function WorkspaceNavIcon({ name }: { name: WorkspaceIconName }) {
  const common = { width: 17, height: 17, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  if (name === "overview") return <svg {...common}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="4" rx="1.5" /><rect x="14" y="11" width="7" height="10" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /></svg>;
  if (name === "recovered") return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="m8 12 2.5 2.5L16.5 8.5" /></svg>;
  if (name === "promises") return <svg {...common}><path d="M7 3v3M17 3v3M4 9h16" /><rect x="4" y="5" width="16" height="16" rx="2" /><path d="m8.5 14 2 2 4.5-4.5" /></svg>;
  if (name === "audit") return <svg {...common}><path d="M9 4h6M9 20h6M12 4v16" /><path d="M5 8h14M5 16h14" /><circle cx="6" cy="8" r="1.5" /><circle cx="18" cy="16" r="1.5" /></svg>;
  if (name === "invoices") return <svg {...common}><path d="M6 3h9l3 3v15H6z" /><path d="M14 3v4h4M9 11h6M9 15h6" /></svg>;
  if (name === "disputes") return <svg {...common}><path d="M12 3 3.5 7v5c0 4.8 3.4 7.8 8.5 9 5.1-1.2 8.5-4.2 8.5-9V7z" /><path d="M12 8v5M12 17h.01" /></svg>;
  if (name === "exceptions") return <svg {...common}><path d="M10.3 4.5 3.2 17a2 2 0 0 0 1.7 3h14.2a2 2 0 0 0 1.7-3L13.7 4.5a2 2 0 0 0-3.4 0Z" /><path d="M12 9v4M12 17h.01" /></svg>;
  if (name === "integrations") return <svg {...common}><path d="M8 12h8M12 8v8" /><path d="M5 3h4v4H5zM15 3h4v4h-4zM5 17h4v4H5zM15 17h4v4h-4z" /></svg>;
  if (name === "settings") return <svg {...common}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" /></svg>;
  if (name === "billing") return <svg {...common}><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 10h18M7 15h3" /></svg>;
  if (name === "team") return <svg {...common}><circle cx="9" cy="8" r="3" /><circle cx="17" cy="10" r="2" /><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6M15 15c3 0 5 2 5 5" /></svg>;
  return <svg {...common}><path d="M4 6h16M7 12h10M10 18h4" /><circle cx="8" cy="6" r="1.5" /><circle cx="15" cy="12" r="1.5" /><circle cx="12" cy="18" r="1.5" /></svg>;
}
