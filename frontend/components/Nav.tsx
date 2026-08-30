"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type IconName = "overview" | "recovered" | "promises" | "audit";

const NAV: Array<{ href: string; label: string; description: string; icon: IconName }> = [
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
              <NavIcon name={item.icon} />
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

function NavIcon({ name }: { name: IconName }) {
  const common = { width: 17, height: 17, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  if (name === "overview") return <svg {...common}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="4" rx="1.5" /><rect x="14" y="11" width="7" height="10" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /></svg>;
  if (name === "recovered") return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="m8 12 2.5 2.5L16.5 8.5" /></svg>;
  if (name === "promises") return <svg {...common}><path d="M7 3v3M17 3v3M4 9h16" /><rect x="4" y="5" width="16" height="16" rx="2" /><path d="m8.5 14 2 2 4.5-4.5" /></svg>;
  return <svg {...common}><path d="M9 4h6M9 20h6M12 4v16" /><path d="M5 8h14M5 16h14" /><circle cx="6" cy="8" r="1.5" /><circle cx="18" cy="16" r="1.5" /></svg>;
}
