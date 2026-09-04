"use client";

import { FormEvent, useEffect, useState } from "react";

import { LiveSignInPrompt } from "@/components/LiveSignInPrompt";
import {
  SettingsAlert,
  SettingsSectionHeader,
  fieldClass,
  labelClass,
  primaryButtonClass,
  secondaryButtonClass,
} from "@/components/LiveSettingsSection";
import { liveGet, livePost, livePut, reauthLive } from "@/lib/live-api";

type Integration = {
  id: string;
  provider: string;
  status: string;
  last_sync_at: string | null;
};

type PaymentConnection = {
  mode: string;
  provider_account_id: string;
  status: string;
  credentials_present: boolean;
  webhook_secret_present?: boolean;
} | null;

type SyncRun = {
  id: string;
  status: string;
  imported_count: number;
  error: string | null;
  started_at: string;
};

/** Zoho's own vocabulary, mapped to something an operator can act on. */
const STATUS_COPY: Record<string, { label: string; tone: "good" | "warn" | "bad" }> = {
  healthy: { label: "Syncing", tone: "good" },
  connected: { label: "Connected", tone: "good" },
  error: { label: "Needs attention", tone: "bad" },
  pending: { label: "Not connected", tone: "warn" },
};

function relativeTime(value: string | null) {
  if (!value) return "never";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export default function LiveIntegrationsPage() {
  const [merchant, setMerchant] = useState("");
  const [zoho, setZoho] = useState<Integration | null>(null);
  const [payment, setPayment] = useState<PaymentConnection>(null);
  // Razorpay Partner OAuth needs credentials Razorpay issues to an approved partner.
  // Without them `oauth/start` answers 503, so the button spends the merchant's
  // password and returns an error they can do nothing about. Assume unavailable
  // until the backend says otherwise, so it is never briefly offered.
  const [oauthAvailable, setOauthAvailable] = useState(false);
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [showKeys, setShowKeys] = useState(false);

  useEffect(() => {
    const value =
      new URLSearchParams(window.location.search).get("merchant") ||
      window.localStorage.getItem("vasooli_live_merchant") ||
      "";
    if (!value) return;
    window.localStorage.setItem("vasooli_live_merchant", value);
    Promise.all([
      liveGet<Integration[]>("/api/live/integrations", value),
      liveGet<PaymentConnection>("/api/live/payment-connections", value),
      liveGet<{ oauth_available: boolean }>(
        "/api/live/payment-connections/capabilities",
        value,
      ).catch(() => ({ oauth_available: false })),
    ])
      .then(([rows, connection, capabilities]) => {
        setMerchant(value);
        setZoho(rows.find((r) => r.provider === "zoho") ?? null);
        setPayment(connection);
        setOauthAvailable(capabilities.oauth_available);
      })
      .catch((cause) => {
        setMerchant(value);
        setError(cause instanceof Error ? cause.message : "Unable to load integrations");
      });
  }, [nonce]);

  useEffect(() => {
    if (!merchant || !zoho) return;
    liveGet<SyncRun[]>(`/api/live/integrations/${zoho.id}/runs`, merchant)
      .then(setRuns)
      .catch(() => setRuns([]));
  }, [merchant, zoho, nonce]);

  async function connect() {
    setBusy("connect");
    setError(null);
    try {
      // GET: the endpoint only builds an authorization URL, it changes nothing.
      const result = await liveGet<{ authorization_url: string }>(
        "/api/live/integrations/zoho/oauth/start",
        merchant,
      );
      window.location.assign(result.authorization_url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to start Zoho authorization");
      setBusy(null);
    }
  }

  async function syncNow() {
    if (!zoho) return;
    setBusy("sync");
    setError(null);
    setMessage(null);
    try {
      const run = await livePost<SyncRun>(
        `/api/live/integrations/${zoho.id}/sync`,
        merchant,
      );
      setMessage(
        run.status === "completed"
          ? `Sync complete — ${run.imported_count} invoice${run.imported_count === 1 ? "" : "s"} imported.`
          : `Sync failed: ${run.error ?? "unknown error"}`,
      );
      setNonce((n) => n + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Sync could not be started");
    } finally {
      setBusy(null);
    }
  }

  async function connectRazorpay(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const password = String(new FormData(event.currentTarget).get("password"));
    setBusy("razorpay");
    setError(null);
    try {
      const proof = await reauthLive(password);
      const result = await liveGet<{ authorization_url: string }>(
        "/api/live/payment-connections/oauth/start",
        merchant,
        { "X-Reauth-Token": proof.reauth_token },
      );
      window.location.assign(result.authorization_url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to connect Razorpay");
      setBusy(null);
    }
  }

  async function connectWithKeys(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy("keys");
    setError(null);
    setMessage(null);
    try {
      const proof = await reauthLive(String(data.get("password")));
      await livePut(
        "/api/live/payment-connections",
        merchant,
        {
          mode: "byok",
          provider_account_id: String(data.get("provider_account_id")),
          api_key_id: String(data.get("api_key_id")),
          api_key_secret: String(data.get("api_key_secret")),
          webhook_secret: String(data.get("webhook_secret")) || null,
        },
        { "X-Reauth-Token": proof.reauth_token },
      );
      form.reset();
      setShowKeys(false);
      setMessage("Razorpay connected. Payment links will be issued on your account.");
      setNonce((n) => n + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Razorpay could not be connected");
    } finally {
      setBusy(null);
    }
  }

  if (!merchant) return <LiveSignInPrompt what="Integrations" />;

  const state = STATUS_COPY[zoho?.status ?? "pending"] ?? STATUS_COPY.pending;
  const connected = Boolean(zoho) && zoho?.status !== "pending";

  return (
    <div className="space-y-5">
      <SettingsSectionHeader
        eyebrow="Connected systems"
        title="Integrations"
        description="Zoho Books brings your invoices in; Razorpay takes the payments out. Credentials are encrypted and never returned by the API."
      />

      <section className="rounded-xl border border-line bg-panel p-5">
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="text-sm font-semibold text-ink">Zoho Books</h3>
          <span
            className={`rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider ${
              state.tone === "good"
                ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                : state.tone === "bad"
                  ? "bg-rose-500/10 text-rose-700 dark:text-rose-300"
                  : "bg-panel-2 text-ink-4"
            }`}
          >
            {state.label}
          </span>
          {connected ? (
            <span className="text-xs text-ink-4">Last sync {relativeTime(zoho?.last_sync_at ?? null)}</span>
          ) : null}
        </div>

        {connected ? (
          <>
            <p className="mt-2 text-sm leading-6 text-ink-3">
              Vasooli reads invoices from Zoho on a schedule and keeps its access token
              refreshed for you. Amounts, payments and cancellations made in Zoho flow through
              to recovery automatically.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <button disabled={busy !== null} onClick={() => void syncNow()} className={primaryButtonClass}>
                {busy === "sync" ? "Syncing…" : "Sync now"}
              </button>
              <button disabled={busy !== null} onClick={() => void connect()} className={secondaryButtonClass}>
                Reconnect
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="mt-2 text-sm leading-6 text-ink-3">
              Connect your Zoho Books organisation to import invoices automatically instead of
              uploading a CSV each time. Vasooli reads invoices, customers, payments and
              cancellations — it never writes back to your ledger.
            </p>
            <button disabled={busy !== null} onClick={() => void connect()} className={`mt-4 ${primaryButtonClass}`}>
              {busy === "connect" ? "Redirecting…" : "Connect Zoho Books"}
            </button>
          </>
        )}
      </section>

      {runs.length ? (
        <section className="overflow-hidden rounded-xl border border-line bg-panel">
          <div className="border-b border-line px-5 py-4">
            <h3 className="text-sm font-semibold text-ink">Recent syncs</h3>
            <p className="mt-1 text-xs text-ink-4">
              Every run is recorded, including the ones that failed.
            </p>
          </div>
          {runs.slice(0, 8).map((run) => (
            <div
              key={run.id}
              className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3 text-sm last:border-0"
            >
              <span
                className={`size-1.5 shrink-0 rounded-full ${
                  run.status === "completed" ? "bg-emerald-500" : "bg-rose-500"
                }`}
              />
              <span className="text-ink-2">
                {run.status === "completed"
                  ? `${run.imported_count} imported`
                  : (run.error ?? "failed")}
              </span>
              <time className="ml-auto shrink-0 text-xs tabular-nums text-ink-4">
                {relativeTime(run.started_at)}
              </time>
            </div>
          ))}
        </section>
      ) : null}

      <section className="rounded-xl border border-line bg-panel p-5">
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="text-sm font-semibold text-ink">Razorpay collections</h3>
          <span
            className={`rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider ${
              payment
                ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                : "bg-panel-2 text-ink-4"
            }`}
          >
            {payment?.status || "Not connected"}
          </span>
        </div>

        {payment ? (
          <p className="mt-2 text-sm leading-6 text-ink-3">
            Payment links are issued on your Razorpay account{" "}
            <span className="font-mono text-xs text-ink-2">{payment.provider_account_id}</span>, so
            customer payments settle directly to you.
          </p>
        ) : (
          <div className="mt-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs leading-5 text-amber-800 dark:text-amber-200">
            <p className="font-semibold">Required before Vasooli can collect for you.</p>
            <p className="mt-1">
              Every payment link is created on your own Razorpay account, so money reaches you
              directly and Vasooli never holds it. Until this is connected, live invoices cannot be
              issued a link and reminders go out without one.
            </p>
          </div>
        )}

        {payment && payment.webhook_secret_present === false ? (
          <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs leading-5 text-amber-800 dark:text-amber-200">
            <p className="font-semibold">Payments will confirm slowly.</p>
            <p className="mt-1">
              No webhook secret is stored, so Razorpay&apos;s instant confirmations cannot be
              verified and payments are only picked up by the hourly reconciliation sweep.
              Add your webhook secret below to confirm in seconds.
            </p>
          </div>
        ) : null}

        {oauthAvailable ? (
          <form onSubmit={connectRazorpay} className="mt-4 flex flex-wrap items-end gap-2">
            <label className={`min-w-52 flex-1 ${labelClass}`}>
              Confirm current password
              <input
                name="password"
                type="password"
                required
                autoComplete="current-password"
                className={fieldClass}
              />
            </label>
            <button disabled={busy !== null} className={primaryButtonClass}>
              {busy === "razorpay" ? "Redirecting…" : payment ? "Reconnect" : "Connect securely"}
            </button>
          </form>
        ) : null}

        <div className={oauthAvailable ? "mt-4 border-t border-line-2 pt-4" : "mt-4"}>
          {showKeys ? (
            <form onSubmit={connectWithKeys} className="space-y-3">
              <p className="text-xs leading-5 text-ink-4">
                From your Razorpay dashboard. Everything here is encrypted before storage and
                never returned by the API.
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className={labelClass}>
                  Account ID
                  <input name="provider_account_id" required placeholder="acc_XXXXXXXX" className={fieldClass} />
                </label>
                <label className={labelClass}>
                  Key ID
                  <input name="api_key_id" required placeholder="rzp_live_XXXXXXXX" className={fieldClass} />
                </label>
                <label className={labelClass}>
                  Key secret
                  <input name="api_key_secret" type="password" required className={fieldClass} />
                </label>
                <label className={labelClass}>
                  Webhook secret
                  <input name="webhook_secret" type="password" className={fieldClass} />
                  <span className="mt-1 block text-xs font-normal text-ink-4">
                    Settings → Webhooks. Without it, payments confirm on the hourly sweep
                    instead of instantly.
                  </span>
                </label>
                <label className={`sm:col-span-2 ${labelClass}`}>
                  Confirm current password
                  <input name="password" type="password" required autoComplete="current-password" className={fieldClass} />
                </label>
              </div>
              <div className="flex flex-wrap gap-2">
                <button disabled={busy !== null} className={primaryButtonClass}>
                  {busy === "keys" ? "Connecting…" : "Save Razorpay keys"}
                </button>
                <button type="button" onClick={() => setShowKeys(false)} className={secondaryButtonClass}>
                  Cancel
                </button>
              </div>
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setShowKeys(true)}
              className="text-xs font-medium text-ink-3 underline underline-offset-4 hover:text-ink"
            >
              Or connect with an API key and secret instead
            </button>
          )}
        </div>
      </section>

      {message ? <SettingsAlert tone="success">{message}</SettingsAlert> : null}
      {error ? <SettingsAlert tone="error">{error}</SettingsAlert> : null}
    </div>
  );
}
