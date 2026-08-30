import Link from "next/link";

export default function LiveSettingsPage() {
  return <main className="mx-auto max-w-5xl px-4 py-14 sm:px-6"><Link href="/live" className="text-sm text-accent">← Setup</Link><h1 className="mt-5 text-3xl font-semibold">Live settings</h1><p className="mt-3 max-w-xl text-ink-3">Configure policy versions, sender domains, suppression, exports, and deletion requests with an audit trail.</p><div className="mt-8 grid gap-4 sm:grid-cols-2"><Link href="/live/integrations" className="rounded-2xl border border-line bg-panel p-5 hover:border-accent"><h2 className="font-semibold">Integrations</h2><p className="mt-2 text-sm text-ink-3">ERP and Razorpay connection state.</p></Link><Link href="/pricing" className="rounded-2xl border border-line bg-panel p-5 hover:border-accent"><h2 className="font-semibold">Plan and limits</h2><p className="mt-2 text-sm text-ink-3">Usage and projected entitlement impact.</p></Link></div></main>;
}
