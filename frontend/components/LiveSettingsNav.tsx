"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { WorkspaceNavIcon, type WorkspaceIconName } from "@/components/Nav";

export const LIVE_SETTINGS_SECTIONS: Array<{
  href: string;
  icon: WorkspaceIconName;
  label: string;
  description: string;
}> = [
  { href: "/live/settings", icon: "settings", label: "General", description: "Sender identity and automation health." },
  { href: "/live/settings/integrations", icon: "integrations", label: "Integrations", description: "Connect Zoho Books and your Razorpay collection account." },
  { href: "/live/settings/policy", icon: "policy", label: "Recovery policy", description: "Control schedules, cooldowns, and escalation limits." },
  { href: "/live/settings/billing", icon: "billing", label: "Billing", description: "Manage the plan, subscription state, and capacity." },
  { href: "/live/settings/team", icon: "team", label: "Team access", description: "Invite teammates and enforce least-privilege roles." },
];

/**
 * `/live/settings` is the index of the section, so an exact match is required —
 * a prefix match would mark General active on every nested page.
 */
export function isActiveSettingsSection(pathname: string, href: string) {
  if (href === "/live/settings") return pathname === href;
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function LiveSettingsNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Settings sections" className="min-w-0 lg:sticky lg:top-6 lg:self-start">
      {/* Horizontally scrollable tabs on small screens, a vertical rail from lg up. */}
      <ul className="-mx-4 flex snap-x gap-1 overflow-x-auto px-4 pb-2 lg:mx-0 lg:flex-col lg:gap-0.5 lg:overflow-visible lg:px-0 lg:pb-0">
        {LIVE_SETTINGS_SECTIONS.map((section) => {
          const active = isActiveSettingsSection(pathname, section.href);
          return (
            <li key={section.href} className="snap-start lg:w-full">
              <Link
                href={section.href}
                aria-current={active ? "page" : undefined}
                title={section.description}
                className={`flex shrink-0 items-center gap-2.5 rounded-lg px-3 py-2 text-xs transition lg:w-full ${
                  active
                    ? "bg-nav-active font-medium text-ink ring-1 ring-inset ring-line"
                    : "text-ink-3 hover:bg-panel-2 hover:text-ink"
                }`}
              >
                <span aria-hidden className="grid size-6 shrink-0 place-items-center rounded-md border border-line">
                  <WorkspaceNavIcon name={section.icon} />
                </span>
                <span className="whitespace-nowrap">{section.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
