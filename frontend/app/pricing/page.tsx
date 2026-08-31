import Link from "next/link";

const PLANS = [
  ["Starter", "₹1,999 / month", "100 active invoices", "5 seats"],
  ["Growth", "₹5,999 / month", "500 active invoices", "15 seats"],
  ["Scale", "₹14,999 / month", "2,000 active invoices", "50 seats"],
];

export default function PricingPage() {
  return (
    <main className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">Live plans</p>
      <h1 className="mt-3 max-w-2xl text-4xl font-semibold tracking-tight">Pricing that follows your active receivables.</h1>
      <p className="mt-4 max-w-2xl text-ink-3">Plans are enforced server-side from verified billing state. No silent downgrades or deleted invoices.</p>
      <div className="mt-10 grid gap-4 md:grid-cols-3">
        {PLANS.map(([name, price, invoices, seats]) => <article key={name} className="rounded-2xl border border-line bg-panel p-6"><h2 className="text-xl font-semibold">{name}</h2><p className="mt-5 text-2xl font-semibold">{price}</p><p className="mt-4 text-sm text-ink-3">{invoices}<br />{seats}</p><Link href="/register" className="mt-8 inline-flex w-full justify-center rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white">Start live</Link></article>)}
      </div>
      <p className="mt-8 text-xs text-ink-4">Taxes, cancellation, refund, retention, and support terms are shown before checkout.</p>
    </main>
  );
}
