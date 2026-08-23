import Link from "next/link";

import { StatusBadge } from "@/components/badges";
import { getPromises } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Page() {
  const promises = await getPromises();
  const groups = {
    active: promises.filter((p) => p.status === "active"),
    kept: promises.filter((p) => p.status === "kept"),
    broken: promises.filter((p) => p.status === "broken"),
  };

  return (
    <div className="space-y-7">
      <div>
        <h1 className="text-lg font-semibold text-ink">Promise tracker</h1>
        <p className="mt-1 text-sm text-ink-3">
          A broken promise resumes escalation at the tier it paused, never back at polite.
        </p>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <Stat label="Active" value={groups.active.length} />
        <Stat label="Kept" value={groups.kept.length} tone="emerald" />
        <Stat label="Broken" value={groups.broken.length} tone="rose" />
      </div>

      <div className="scroll-x rounded-xl border border-line">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="border-b border-line text-left text-xs uppercase tracking-wider text-ink-3">
            <tr>
              <th className="px-4 py-2.5 font-medium">Invoice</th>
              <th className="px-4 py-2.5 font-medium">Customer</th>
              <th className="px-4 py-2.5 font-medium">Promised by</th>
              <th className="px-4 py-2.5 text-right font-medium">Amount</th>
              <th className="px-4 py-2.5 font-medium">Status</th>
              <th className="px-4 py-2.5 font-medium">Resumes at</th>
              <th className="px-4 py-2.5 font-medium">Their words</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line-2">
            {promises.map((p) => (
              <tr key={p.id} className="hover:bg-panel-2">
                <td className="px-4 py-2.5 font-mono text-[13px] text-accent">
                  {p.invoice_number}
                </td>
                <td className="px-4 py-2.5 text-ink-2">{p.customer_name}</td>
                <td className="px-4 py-2.5 tabular-nums text-ink-2">{p.promised_date}</td>
                <td className="px-4 py-2.5 text-right tabular-nums text-ink">
                  {p.amount_display}
                </td>
                <td className="px-4 py-2.5">
                  <StatusBadge status={p.status} />
                </td>
                <td className="px-4 py-2.5 text-ink-3">Tier {p.tier_at_pause}</td>
                <td className="max-w-[280px] truncate px-4 py-2.5 text-xs italic text-ink-3">
                  “{p.excerpt}”
                </td>
              </tr>
            ))}
            {promises.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-sm text-ink-3">
                  No promises yet. Open an invoice and simulate a reply.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <Link href="/" className="inline-block text-xs text-ink-3 hover:text-ink-2">
        ← Recovery queue
      </Link>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  const color =
    tone === "emerald" ? "text-emerald-700 dark:text-emerald-300" : tone === "rose" ? "text-rose-700 dark:text-rose-300" : "text-ink";
  return (
    <div className="rounded-xl border border-line bg-panel px-5 py-4">
      <div className="text-xs uppercase tracking-wider text-ink-3">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}
