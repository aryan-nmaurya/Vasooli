import { ProvenanceBadge } from "@/components/badges";
import { getAudit } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Page() {
  const entries = await getAudit();

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold text-ink">Audit log</h1>
        <p className="mt-1 text-sm text-ink-3">
          Append-only. A database trigger rejects any UPDATE or DELETE on this table, for
          every role — so what is here is what happened.
        </p>
      </div>

      <div className="scroll-x rounded-xl border border-line">
        <table className="w-full min-w-[820px] text-sm">
          <thead className="border-b border-line text-left text-xs uppercase tracking-wider text-ink-3">
            <tr>
              <th className="px-4 py-2.5 font-medium">When</th>
              <th className="px-4 py-2.5 font-medium">By</th>
              <th className="px-4 py-2.5 font-medium">Invoice</th>
              <th className="px-4 py-2.5 font-medium">What happened</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line-2">
            {entries.map((e, i) => (
              <tr key={i} className="hover:bg-panel-2">
                <td className="whitespace-nowrap px-4 py-2 font-mono text-[11px] text-ink-4">
                  {new Date(e.at).toLocaleString("en-IN", {
                    day: "2-digit",
                    month: "short",
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })}
                </td>
                <td className="px-4 py-2">
                  <ProvenanceBadge provenance={e.provenance} />
                </td>
                <td className="px-4 py-2 font-mono text-[12px] text-accent">
                  {e.invoice_number ?? "—"}
                </td>
                <td className="px-4 py-2 text-ink-2">{e.summary}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
