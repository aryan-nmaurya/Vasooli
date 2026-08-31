"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { LiveSignInPrompt } from "@/components/LiveSignInPrompt";

import { liveGet } from "@/lib/live-api";

type Readiness = { status: string; database: boolean; jobs: Record<string, { status: string; stale: boolean }> };

export default function LiveReadinessPage() {
  const [merchant, setMerchant] = useState("");
  const [data, setData] = useState<Readiness | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { const value = window.localStorage.getItem("vasooli_live_merchant") || ""; Promise.resolve().then(() => setMerchant(value)); if (value) liveGet<Readiness>("/api/live/operations/readiness", value).then(setData).catch((cause) => setError(cause instanceof Error ? cause.message : "Unable to load readiness")); }, []);
  return <main className="mx-auto max-w-4xl"><Link href="/live" className="text-sm text-accent">← Dashboard</Link><h1 className="mt-5 text-3xl font-semibold">System health</h1><p className="mt-2 text-ink-3">Diagnostic history for the database and background recovery jobs. This is operational evidence, not an onboarding gate.</p>{!merchant ? <LiveSignInPrompt what="System health" /> : data ? <div className="mt-8 space-y-3"><div className="rounded-2xl border border-line bg-panel p-5"><p className="text-sm font-semibold">Overall: <span className="capitalize">{data.status}</span></p><p className="mt-1 text-xs text-ink-4">Database: {data.database ? "healthy" : "unavailable"}</p></div>{Object.entries(data.jobs).map(([name, job]) => <div key={name} className="flex items-center justify-between rounded-xl border border-line bg-panel px-4 py-3 text-sm"><span>{name.replaceAll("_", " ")}</span><span className={job.stale || job.status === "failed" ? "text-rose-700" : "text-emerald-700"}>{job.status}{job.stale ? " · stale" : ""}</span></div>)}</div> : <p className="mt-8 text-sm text-ink-4">{"Loading…"}</p>}{error ? <p role="alert" className="mt-4 text-sm text-rose-700">{error}</p> : null}</main>;
}
