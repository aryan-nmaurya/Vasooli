"use client";

/**
 * Primary navigation.
 *
 * Separated from the wordmark by a divider, and the current page is highlighted.
 * Without that, the tagline sitting inline with these links read as four more menu
 * items — the periods in "Chase. Track. Reconcile. Recover." made it look like a
 * list of destinations.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/promises", label: "Promises" },
  { href: "/audit", label: "Audit log" },
];

export function Nav() {
  const pathname = usePathname();

  return (
    <nav className="flex items-center gap-1 text-sm">
      {NAV.map((item) => {
        const active =
          item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={`rounded-md px-3 py-1.5 transition ${
              active
                ? "bg-panel-2 font-medium text-ink"
                : "text-ink-3 hover:bg-panel-2 hover:text-ink"
            }`}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
