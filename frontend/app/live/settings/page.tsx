"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { liveGet, livePost } from "@/lib/live-api";

type Domain = { id: string; domain: string; local_part: string; status: string; dns_records: Array<{ type: string; name: string; value: string }> };

export default function LiveSettingsPage() {
  const [merchant, setMerchant] = useState("");
  const [domains, setDomains] = useState<Domain[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async (id: string) => setDomains(await liveGet<Domain[]>("/api/live/controls/sending-domains", id)), []);

  useEffect(() => {
    const id = window.localStorage.getItem("vasooli_live_merchant") || "";
    Promise.resolve().then(async () => {
      setMerchant(id);
      if (id) await load(id).catch((cause) => setError(cause instanceof Error ? cause.message : "Unable to load settings"));
    });
  }, [load]);

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!merchant) return;
    const form = event.currentTarget;
    const data = new FormData(form); const domain = String(data.get("domain")); const localPart = String(data.get("local_part"));
    setBusy(true); setError(null);
    try { await livePost(`/api/live/controls/sending-domains?domain=${encodeURIComponent(domain)}&local_part=${encodeURIComponent(localPart)}`, merchant); form.reset(); await load(merchant); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Domain could not be added"); }
    finally { setBusy(false); }
  }

  async function verify(id: string) {
    setBusy(true); setError(null);
    try { await livePost(`/api/live/controls/sending-domains/${id}/verify`, merchant); await load(merchant); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Verification failed"); }
    finally { setBusy(false); }
  }

  return <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6"><h1 className="text-3xl font-semibold">Live settings</h1><p className="mt-2 max-w-2xl text-ink-3">Register and verify the sender identity used for customer communication. Resend DNS records are provisioned server-side.</p><div className="mt-7 grid gap-5 lg:grid-cols-[0.8fr_1.2fr]"><form onSubmit={add} className="h-fit rounded-2xl border border-line bg-panel p-5"><h2 className="font-semibold">Add sending domain</h2><label className="mt-4 block text-sm">Domain<input name="domain" required placeholder="billing.example.com" className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label><label className="mt-3 block text-sm">From address<input name="local_part" required defaultValue="accounts" className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /><span className="mt-1 block text-xs text-ink-4">For example, accounts@billing.example.com</span></label><button disabled={!merchant || busy} className="mt-4 rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">{busy ? "Working…" : "Register sender domain"}</button><p className="mt-3 text-xs text-ink-4">Only provider-verified domains can be used for live delivery.</p></form><section className="space-y-3">{domains.length ? domains.map((domain) => <article key={domain.id} className="rounded-2xl border border-line bg-panel p-5"><div className="flex items-center gap-3"><div><h2 className="font-semibold">{domain.local_part}@{domain.domain}</h2><p className="text-xs text-ink-4">Customer-facing From address</p></div><span className={`ml-auto rounded-full px-2.5 py-1 text-xs capitalize ${domain.status === "verified" ? "bg-emerald-500/10 text-emerald-700" : "bg-amber-500/10 text-amber-700"}`}>{domain.status}</span></div>{domain.dns_records.map((record) => <dl key={`${record.type}-${record.name}`} className="mt-4 grid gap-1 rounded-lg bg-surface p-3 font-mono text-xs"><dt className="text-ink-4">{record.type} · {record.name}</dt><dd className="break-all text-ink-2">{record.value}</dd></dl>)}{domain.status !== "verified" ? <button type="button" disabled={busy} onClick={() => void verify(domain.id)} className="mt-4 rounded-lg border border-line px-3 py-2 text-sm font-semibold hover:border-accent disabled:opacity-50">Check provider verification</button> : null}</article>) : <div className="rounded-2xl border border-dashed border-line p-6 text-sm text-ink-4">{merchant ? "No sending domains configured." : "Sign in to configure a live workspace."}</div>}</section></div><div className="mt-7 grid gap-3 sm:grid-cols-3"><Link href="/live/policy" className="rounded-xl border border-line bg-panel p-4 hover:border-accent"><span className="font-semibold">Recovery policy</span><p className="mt-1 text-xs text-ink-4">Versioned schedule and limits</p></Link><Link href="/live/integrations" className="rounded-xl border border-line bg-panel p-4 hover:border-accent"><span className="font-semibold">Financial integrations</span><p className="mt-1 text-xs text-ink-4">ERP connection state</p></Link><Link href="/live/readiness" className="rounded-xl border border-line bg-panel p-4 hover:border-accent"><span className="font-semibold">Operational readiness</span><p className="mt-1 text-xs text-ink-4">Database and scheduler health</p></Link></div>{error ? <p role="alert" className="mt-5 rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">{error}</p> : null}</main>;
}
