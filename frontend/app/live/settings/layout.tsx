import type { ReactNode } from "react";

import { LiveSettingsNav } from "@/components/LiveSettingsNav";

/**
 * One shell for every settings section. The page title and the section rail live
 * here so navigating between sections never drops the surrounding context — the
 * previous hub-of-cards layout stranded each section on a page with no way back.
 */
export default function LiveSettingsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="space-y-6">
      <header>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">
          Workspace control center
        </p>
        <h1 className="text-2xl font-semibold tracking-[-0.03em] text-ink sm:text-[1.75rem]">Settings</h1>
        <p className="mt-1.5 max-w-3xl text-sm leading-6 text-ink-3">
          Configure sender identity, connected financial systems, recovery rules, billing, and team access.
        </p>
      </header>

      {/*
        The base track is minmax(0,1fr), not the implicit `auto`: an auto track is
        sized by its widest child, so the horizontally scrollable section rail would
        stretch the grid and scroll the whole page sideways on mobile.
      */}
      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-8">
        <LiveSettingsNav />
        <div className="min-w-0">{children}</div>
      </div>
    </div>
  );
}
