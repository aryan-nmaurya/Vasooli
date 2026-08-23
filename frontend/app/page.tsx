import { OverviewClient } from "@/components/Overview";
import { getOverview, getQueue } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Page() {
  try {
    const [overview, queue] = await Promise.all([getOverview(), getQueue()]);
    return <OverviewClient initialOverview={overview} initialQueue={queue} />;
  } catch {
    return (
      <div className="rounded-xl border border-rose-300 bg-rose-50 px-5 py-4 text-sm text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
        Cannot reach the backend. Start it with{" "}
        <code className="font-mono text-rose-900 dark:text-rose-100">uv run uvicorn app.main:app --reload</code>, or
        set <code className="font-mono text-rose-900 dark:text-rose-100">NEXT_PUBLIC_API_URL</code>.
      </div>
    );
  }
}
