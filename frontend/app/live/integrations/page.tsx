"use client";

import { FormEvent, useEffect, useState } from "react";

import { liveGet, livePost, livePut, reauthLive } from "@/lib/live-api";

type Integration = { id: string; provider: string; status: string; last_sync_at: string | null };
type PaymentConnection = { mode: string; provider_account_id: string; status: string; credentials_present: boolean } | null;

export default function LiveIntegrationsPage() {
  const [merchant, setMerchant] = useState("");
  const [rows, setRows] = useState<Integration[]>([]);
  const [payment, setPayment] = useState<PaymentConnection>(null);
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
      Promise.all([liveGet<Integration[]>("/api/live/integrations", value), liveGet<PaymentConnection>("/api/live/payment-connections", value)])
        .then(([integrations, connection]) => { setRows(integrations); setPayment(connection); })
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

  async function connectRazorpay(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!merchant) return;
    const password = String(new FormData(event.currentTarget).get("password"));
    setBusy(true); setError(null);
    try { const proof = await reauthLive(password); const result = await livePost<{ authorization_url: string }>("/api/live/payment-connections/oauth/start", merchant, undefined, { "X-Reauth-Token": proof.reauth_token }); window.location.assign(result.authorization_url); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to connect Razorpay"); setBusy(false); }
  }

  async function connectFeed(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!merchant) return;
    const form = event.currentTarget; const data = new FormData(form); const provider = String(data.get("provider"));
    setBusy(true); setError(null);
    try { const proof = await reauthLive(String(data.get("password"))); const credentials = JSON.parse(String(data.get("credentials") || "{}")); await livePut("/api/live/integrations", merchant, { provider, source_tenant: String(data.get("source_tenant")), credentials }, { "X-Reauth-Token": proof.reauth_token }); setRows(await liveGet<Integration[]>("/api/live/integrations", merchant)); form.reset(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Integration could not be saved"); }
    finally { setBusy(false); }
  }

  return <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6"><h1 className="text-3xl font-semibold">Integrations</h1><p className="mt-2 text-ink-3">Connect the financial systems used for receivables and verified collections. Secrets are encrypted and never returned.</p><div className="mt-7 grid gap-4 lg:grid-cols-2"><article className="rounded-2xl border border-line bg-panel p-5"><h2 className="text-lg font-semibold">Zoho Books</h2><p className="mt-2 text-sm text-ink-3">{rows.find((row) => row.provider === "zoho")?.status || "Not connected"}</p><button disabled={busy || !merchant} onClick={() => connect("zoho")} className="mt-5 rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">Connect with OAuth</button></article><article className="rounded-2xl border border-line bg-panel p-5"><h2 className="text-lg font-semibold">Razorpay collections</h2><p className="mt-2 text-sm text-ink-3">{payment ? `${payment.status} · ${payment.provider_account_id}` : "Not connected"}</p><form onSubmit={connectRazorpay} className="mt-4 flex flex-wrap gap-2"><input name="password" type="password" required aria-label="Current password for Razorpay connection" placeholder="Current password" className="min-w-52 flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-sm" /><button disabled={busy || !merchant} className="rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">Connect securely</button></form></article></div><form onSubmit={connectFeed} className="mt-5 rounded-2xl border border-line bg-panel p-5"><div><h2 className="text-lg font-semibold">Signed custom ERP feed</h2><p className="mt-1 text-sm text-ink-3">Push invoice updates through a replay-safe webhook authenticated with your shared secret.</p></div><input type="hidden" name="provider" value="custom" /><div className="mt-4 grid gap-3 sm:grid-cols-2"><label className="text-sm">Source tenant<input name="source_tenant" required className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label><label className="text-sm">Confirm current password<input name="password" type="password" required className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label><label className="text-sm sm:col-span-2">Credentials JSON<textarea name="credentials" required defaultValue={'{"shared_secret":""}'} className="mt-1 min-h-20 w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-xs" /></label></div><button disabled={busy || !merchant} className="mt-4 rounded-lg border border-line px-4 py-2.5 text-sm font-semibold hover:border-accent disabled:opacity-50">Save encrypted connection</button></form>{error ? <p role="alert" className="mt-5 rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">{error}</p> : null}</main>;
}
