"use client";

import { FormEvent, useEffect, useState } from "react";

import { LiveSignInPrompt } from "@/components/LiveSignInPrompt";
import {
  SettingsAlert,
  SettingsCard,
  SettingsSectionHeader,
  fieldClass,
  labelClass,
  primaryButtonClass,
  secondaryButtonClass,
} from "@/components/LiveSettingsSection";
import { liveGet, livePost, livePut, reauthLive } from "@/lib/live-api";

type Integration = { id: string; provider: string; status: string; last_sync_at: string | null };
type PaymentConnection = {
  mode: string;
  provider_account_id: string;
  status: string;
  credentials_present: boolean;
} | null;

function StatusPill({ connected, children }: { connected: boolean; children: React.ReactNode }) {
  return (
    <span
      className={`rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider ${
        connected
          ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
          : "bg-panel-2 text-ink-4"
      }`}
    >
      {children}
    </span>
  );
}

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
      Promise.all([
        liveGet<Integration[]>("/api/live/integrations", value),
        liveGet<PaymentConnection>("/api/live/payment-connections", value),
      ])
        .then(([integrations, connection]) => {
          setRows(integrations);
          setPayment(connection);
        })
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
    event.preventDefault();
    if (!merchant) return;
    const password = String(new FormData(event.currentTarget).get("password"));
    setBusy(true);
    setError(null);
    try {
      const proof = await reauthLive(password);
      const result = await livePost<{ authorization_url: string }>(
        "/api/live/payment-connections/oauth/start",
        merchant,
        undefined,
        { "X-Reauth-Token": proof.reauth_token },
      );
      window.location.assign(result.authorization_url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to connect Razorpay");
      setBusy(false);
    }
  }

  async function connectFeed(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!merchant) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const provider = String(data.get("provider"));
    setBusy(true);
    setError(null);
    try {
      const proof = await reauthLive(String(data.get("password")));
      const credentials = JSON.parse(String(data.get("credentials") || "{}"));
      await livePut(
        "/api/live/integrations",
        merchant,
        { provider, source_tenant: String(data.get("source_tenant")), credentials },
        { "X-Reauth-Token": proof.reauth_token },
      );
      setRows(await liveGet<Integration[]>("/api/live/integrations", merchant));
      form.reset();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Integration could not be saved");
    } finally {
      setBusy(false);
    }
  }

  if (!merchant) return <LiveSignInPrompt what="Integrations" />;

  const zoho = rows.find((row) => row.provider === "zoho");

  return (
    <div className="space-y-5">
      <SettingsSectionHeader
        eyebrow="Connected systems"
        title="Integrations"
        description="Connect the financial systems used for receivables and verified collections. Secrets are encrypted and never returned."
      />

      <div className="grid gap-4 xl:grid-cols-2">
        <article className="flex flex-col rounded-xl border border-line bg-panel p-5">
          <div className="flex items-center gap-3">
            <h3 className="text-sm font-semibold text-ink">Zoho Books</h3>
            <StatusPill connected={Boolean(zoho)}>{zoho?.status || "Not connected"}</StatusPill>
          </div>
          <p className="mt-2 text-sm leading-6 text-ink-3">
            Sync invoices and customers straight from your ledger.
          </p>
          <button
            disabled={busy}
            onClick={() => void connect("zoho")}
            className={`mt-5 self-start ${primaryButtonClass}`}
          >
            Connect with OAuth
          </button>
        </article>

        <article className="flex flex-col rounded-xl border border-line bg-panel p-5">
          <div className="flex items-center gap-3">
            <h3 className="text-sm font-semibold text-ink">Razorpay collections</h3>
            <StatusPill connected={Boolean(payment)}>{payment?.status || "Not connected"}</StatusPill>
          </div>
          <p className="mt-2 text-sm leading-6 text-ink-3">
            {payment
              ? `Account ${payment.provider_account_id}`
              : "Required before live payment links can be issued."}
          </p>
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
            <button disabled={busy} className={primaryButtonClass}>
              Connect securely
            </button>
          </form>
        </article>
      </div>

      <SettingsCard
        title="Signed custom ERP feed"
        hint="Push invoice updates through a replay-safe webhook authenticated with your shared secret."
      >
        <form onSubmit={connectFeed} className="space-y-4">
          <input type="hidden" name="provider" value="custom" />
          <div className="grid gap-4 sm:grid-cols-2">
            <label className={labelClass}>
              Source tenant
              <input name="source_tenant" required className={fieldClass} />
            </label>
            <label className={labelClass}>
              Confirm current password
              <input
                name="password"
                type="password"
                required
                autoComplete="current-password"
                className={fieldClass}
              />
            </label>
            <label className={`sm:col-span-2 ${labelClass}`}>
              Credentials JSON
              <textarea
                name="credentials"
                required
                defaultValue={'{"shared_secret":""}'}
                className={`${fieldClass} min-h-20 font-mono text-xs`}
              />
            </label>
          </div>
          <button disabled={busy} className={secondaryButtonClass}>
            Save encrypted connection
          </button>
        </form>
      </SettingsCard>

      {error ? <SettingsAlert tone="error">{error}</SettingsAlert> : null}
    </div>
  );
}
