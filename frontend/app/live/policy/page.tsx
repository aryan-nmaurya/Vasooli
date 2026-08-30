"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";

import { liveGet, livePut } from "@/lib/live-api";

type Policy = { tier_offsets: number[]; cooldown_days: number; max_attempts: number; timezone: string; version: number };

export default function LivePolicyPage() {
  const [merchant, setMerchant] = useState("");
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => { const value = window.localStorage.getItem("vasooli_live_merchant") || ""; Promise.resolve().then(() => setMerchant(value)); if (value) liveGet<Policy | null>("/api/live/controls/policy", value).then(setPolicy).catch((cause) => setError(cause instanceof Error ? cause.message : "Unable to load policy")); }, []);
  async function save(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); try { const result = await livePut<Policy>("/api/live/controls/policy", merchant, { preset: String(data.get("preset")), timezone: String(data.get("timezone")) }); setPolicy(result); setMessage(`Policy v${result.version} saved.`); } catch (cause) { setError(cause instanceof Error ? cause.message : "Policy save failed"); } }
  return <main className="mx-auto max-w-4xl px-4 py-14 sm:px-6"><Link href="/live" className="text-sm text-accent">← Setup</Link><h1 className="mt-5 text-3xl font-semibold">Recovery policy</h1><p className="mt-2 text-ink-3">Offsets are absolute days overdue. Save-time validation rejects schedules that cannot fire.</p><form onSubmit={save} className="mt-8 max-w-xl space-y-4 rounded-2xl border border-line bg-panel p-6"><label className="block text-sm">Preset<select name="preset" defaultValue="default" className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2"><option value="default">Default — 3 / 10 / 21, cooldown 7</option><option value="3_7_14">Fast — 3 / 7 / 14, cooldown 4</option></select></label><label className="block text-sm">Timezone<input name="timezone" defaultValue={policy?.timezone || "Asia/Kolkata"} className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label><button disabled={!merchant} className="rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">Save new policy version</button></form>{policy ? <div className="mt-6 rounded-2xl border border-line bg-panel p-5 text-sm"><p className="font-semibold">Active policy v{policy.version}</p><p className="mt-2 text-ink-3">Days {policy.tier_offsets.join(" / ")} · cooldown {policy.cooldown_days} · max attempts {policy.max_attempts}</p></div> : null}{message ? <p className="mt-4 text-sm text-emerald-700">{message}</p> : null}{error ? <p role="alert" className="mt-4 text-sm text-rose-700">{error}</p> : null}</main>;
}
