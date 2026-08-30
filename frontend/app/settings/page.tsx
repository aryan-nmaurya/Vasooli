import { DemoSettingsPanel } from "@/components/DemoSettings";
import { getDemoClock } from "@/lib/api";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Workspace settings — Vasooli",
  description: "Manage Vasooli demo controls and reminder routing.",
};

export default async function SettingsPage() {
  const clock = await getDemoClock().catch(() => null);

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

      {clock ? (
        <DemoSettingsPanel initial={clock} />
      ) : (
        <div className="rounded-xl border border-line bg-panel p-5 text-sm text-ink-3">
          Demo controls are unavailable in this environment.
        </div>
      )}
    </div>
  );
}
