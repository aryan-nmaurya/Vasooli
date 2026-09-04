import { notFound } from "next/navigation";

import { DemoSettingsPanel } from "@/components/DemoSettings";
import { getDemoClock } from "@/lib/api";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Workspace settings — Vasooli",
  description: "Manage Vasooli demo controls and reminder routing.",
};

export default async function SettingsPage() {
  const clock = await getDemoClock().catch(() => null);
  // This page is the demo controls and nothing else. Where they are switched off —
  // production, always — there is no page here to show. It used to render "Demo
  // controls are unavailable in this environment", which is a dead end reachable
  // from the sidebar: a merchant on a live deployment could click Workspace settings
  // and land on a screen that exists only to say it does not apply to them.
  if (!clock) notFound();

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <header>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">
          Workspace
        </p>
        <h1 className="text-2xl font-semibold tracking-[-0.03em] text-ink sm:text-[1.75rem]">
          Settings
        </h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-6 text-ink-3">
          Configure the review environment without covering the recovery dashboard.
        </p>
      </header>

      <DemoSettingsPanel initial={clock} />
    </div>
  );
}
