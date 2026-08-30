import Link from "next/link";

export default function LiveOnboardingPage() {
  const steps = [
    ["1", "Verify identity", "Email verification and workspace owner are complete after registration."],
    ["2", "Choose a plan", "Select a server-enforced plan and complete Razorpay subscription checkout."],
    ["3", "Connect data", "Run a read-only ERP sync before any recovery action is enabled."],
    ["4", "Verify sending and collections", "Confirm your sender domain and merchant Razorpay connection."],
  ];
  return <main className="mx-auto max-w-5xl px-4 py-14 sm:px-6"><p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">LIVE workspace</p><h1 className="mt-3 text-4xl font-semibold tracking-tight">Finish setup before automation.</h1><p className="mt-4 max-w-2xl text-ink-3">Every step is resumable and gates the next one. Payment or email is never inferred from a browser redirect.</p><div className="mt-10 grid gap-4 sm:grid-cols-2">{steps.map(([number, title, body]) => <article key={number} className="rounded-2xl border border-line bg-panel p-5"><span className="text-sm font-semibold text-accent">{number}</span><h2 className="mt-3 text-lg font-semibold">{title}</h2><p className="mt-2 text-sm text-ink-3">{body}</p></article>)}</div><div className="mt-8 flex flex-wrap gap-3"><Link href="/live/billing" className="rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white">Open billing</Link><Link href="/live/integrations" className="rounded-lg border border-line px-4 py-2.5 text-sm font-semibold">Connect integrations</Link><Link href="/live/invoices" className="rounded-lg border border-line px-4 py-2.5 text-sm font-semibold">Open invoices</Link><Link href="/live/policy" className="rounded-lg border border-line px-4 py-2.5 text-sm font-semibold">Edit policy</Link><Link href="/live/team" className="rounded-lg border border-line px-4 py-2.5 text-sm font-semibold">Manage team</Link></div></main>;
}
