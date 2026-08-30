import Link from "next/link";

export default function LiveTeamPage() {
  return <main className="mx-auto max-w-5xl px-4 py-14 sm:px-6"><Link href="/live" className="text-sm text-accent">← Setup</Link><h1 className="mt-5 text-3xl font-semibold">Team access</h1><p className="mt-3 max-w-xl text-ink-3">Invite people with least-privilege roles. Billing, refunds, ERP credentials, and recovery actions stay separately permissioned.</p><div className="mt-8 rounded-2xl border border-line bg-panel p-6"><p className="text-sm text-ink-3">Owner and admin controls are available after live sign in.</p></div></main>;
}
