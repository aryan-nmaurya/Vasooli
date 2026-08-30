import Link from "next/link";

import { ExportMenu } from "@/components/ExportMenu";
import { getQueue } from "@/lib/api";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Recovered — Vasooli",
  description: "Invoices whose money actually arrived.",
};

function when(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export default async function RecoveredPage() {
  // Filtered server-side by the same queue endpoint the overview uses, so a recovered
  // invoice cannot appear here with different numbers than it shows there.
  const rows = (await getQueue("&status=recovered")).filter((r) => r.status === "recovered");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">Collections</p>
          <h1 className="text-2xl font-semibold tracking-[-0.03em] text-ink sm:text-[1.75rem]">Recovered</h1>
          <p className="mt-1.5 text-sm leading-6 text-ink-3">
            {rows.length === 0
              ? "Nothing recovered yet."
              : `${rows.length} invoice${rows.length === 1 ? "" : "s"} settled — confirmed by Razorpay, not by a model.`}
          </p>
        </div>
        <ExportMenu
          groups={[
            {
              dataset: "recovered",
              label: "Recovered invoices",
              hint: `${rows.length} settled, with dates and days to recovery`,
            },
          ]}
        />
      </div>

      {rows.length === 0 ? (
        <p className="rounded-xl border border-line bg-panel px-4 py-10 text-center text-sm text-ink-3">
          When a payment is confirmed, the invoice closes and appears here.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-line bg-panel shadow-sm">
          <table className="w-full min-w-[46rem] text-sm">
            <thead>
              <tr className="border-b border-line text-left">
                {["Invoice", "Customer", "Amount", "Recovered", "Reason", ""].map((h, i) => (
                  <th
                    key={h || i}
                    className={`px-4 py-2.5 text-xs font-medium uppercase tracking-wider text-ink-3 ${
                      h === "Amount" ? "text-right" : ""
                    }`}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line-2">
              {rows.map((row) => (
                <tr key={row.id} className="transition hover:bg-panel-2">
                  <td className="px-4 py-2.5">
                    <Link
                      href={`/invoices/${row.id}`}
                      className="font-mono text-[13px] text-accent hover:underline"
                    >
                      {row.invoice_number}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5 text-ink-2">{row.customer_name}</td>
                  <td className="px-4 py-2.5 text-right font-medium tabular-nums text-ink">
                    {row.amount_display}
                  </td>
                  <td className="px-4 py-2.5 tabular-nums text-ink-3">
                    {when(row.recovered_at)}
                  </td>
                  <td className="px-4 py-2.5 text-ink-3">{row.reason_category ?? "—"}</td>
                  <td className="max-w-[22rem] px-4 py-2.5 text-xs text-ink-3">{row.why}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
