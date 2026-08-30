"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { liveGet, livePost } from "@/lib/live-api";

type Integration = { id: string; provider: string; status: string; last_sync_at: string | null };

export default function LiveIntegrationsPage() {
  const [merchant, setMerchant] = useState("");
  const [rows, setRows] = useState<Integration[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const value =
      new URLSearchParams(window.location.search).get("merchant") ||
      window.localStorage.getItem("vasooli_live_merchant") ||
      "";
    Promise.resolve().then(() => setMerchant(value));
    if (value) {
      window.localStorage.setItem("vasooli_live_merchant", value);
      liveGet<Integration[]>("/api/live/integrations", value)
        .then(setRows)
        .catch((cause) =>
          setError(cause instanceof Error ? cause.message : "Unable to load integrations"),
        );
    }
  }, []);
  async function connect(provider: string) {
    if (!merchant) {
      setError("Sign in to a live workspace first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await livePost<{ authorization_url: string }>(
        `/api/live/integrations/${provider}/oauth/start`,
        merchant,
      );
      window.location.assign(result.authorization_url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to start OAuth");
    } finally {
      setBusy(false);
    }
  }
  return <main className="mx-auto max-w-5xl px-4 py-14 sm:px-6"><Link href="/live" className="text-sm text-accent">← Setup</Link><h1 className="mt-5 text-3xl font-semibold">Integrations</h1><p className="mt-3 text-ink-3">Read-only ERP sync comes first. Credentials are encrypted and provider tokens are never returned.</p><div className="mt-8 grid gap-4 sm:grid-cols-3">{["zoho", "tally", "custom"].map((provider) => <article key={provider} className="rounded-2xl border border-line bg-panel p-5"><h2 className="text-lg font-semibold capitalize">{provider}</h2><p className="mt-2 text-sm text-ink-3">{rows.find((row) => row.provider === provider)?.status || "Not connected"}</p>{provider === "zoho" ? <button disabled={busy} onClick={() => connect(provider)} className="mt-5 rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">Connect with OAuth</button> : provider === "tally" ? <p className="mt-5 text-xs text-ink-4">Install the outbound edge agent; Vasooli never exposes Tally port 9000.</p> : <p className="mt-5 text-xs text-ink-4">Use the signed webhook endpoint for custom feeds.</p>}</article>)}</div>{error ? <p role="alert" className="mt-5 rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">{error}</p> : null}</main>;
}
