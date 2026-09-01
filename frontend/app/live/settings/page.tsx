"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

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
import { liveGet, livePost } from "@/lib/live-api";

type Domain = {
  id: string;
  domain: string;
  local_part: string;
  status: string;
  dns_records: Array<{ type: string; name: string; value: string }>;
};

type Automation = {
  status: "ready" | "attention" | "degraded";
  summary: string;
  jobs: Record<string, { last_started_at: string | null; status: string; stale: boolean }>;
};

const JOB_LABELS: Record<string, string> = {
  recovery_cycle: "Daily recovery cycle",
  payment_link_sync: "Razorpay payment sync",
  retry_operations: "Retry sweep",
  service_heartbeat: "Service heartbeat",
  billing_reconciliation: "Billing reconciliation",
};

function relativeTime(value: string | null) {
  if (!value) return "Never run";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return "Just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export default function LiveSettingsGeneralPage() {
  const [merchant, setMerchant] = useState("");
  const [domains, setDomains] = useState<Domain[]>([]);
  const [automation, setAutomation] = useState<Automation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (id: string) => {
    const [domainResult, automationResult] = await Promise.allSettled([
      liveGet<Domain[]>("/api/live/controls/sending-domains", id),
      liveGet<Automation>("/api/live/operations/readiness", id),
    ]);
    if (domainResult.status === "fulfilled") setDomains(domainResult.value);
    if (automationResult.status === "fulfilled") setAutomation(automationResult.value);
    setError(
      domainResult.status === "rejected" || automationResult.status === "rejected"
        ? "Some settings could not be refreshed. Your saved configuration has not changed."
        : null,
    );
  }, []);

  useEffect(() => {
    const id = window.localStorage.getItem("vasooli_live_merchant") || "";
    Promise.resolve().then(() => {
      setMerchant(id);
      if (id) void load(id);
    });
  }, [load]);

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!merchant) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const domain = String(data.get("domain"));
    const localPart = String(data.get("local_part"));
    setBusy(true);
    setError(null);
    try {
      await livePost(
        `/api/live/controls/sending-domains?domain=${encodeURIComponent(domain)}&local_part=${encodeURIComponent(localPart)}`,
        merchant,
      );
      form.reset();
      await load(merchant);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Domain could not be added");
    } finally {
      setBusy(false);
    }
  }

  async function verify(id: string) {
    setBusy(true);
    setError(null);
    try {
      await livePost(`/api/live/controls/sending-domains/${id}/verify`, merchant);
      await load(merchant);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Verification failed");
    } finally {
      setBusy(false);
    }
  }

  if (!merchant) return <LiveSignInPrompt what="Workspace settings" />;

  return (
    <div className="space-y-8">
      <section className="space-y-4">
        <SettingsSectionHeader
          eyebrow="Automation"
          title="Background jobs"
          description="Scheduler activity for recovery, reconciliation, and retries. Read-only — schedules are set in Recovery policy."
        />
        <AutomationPanel automation={automation} />
      </section>

      {error ? <SettingsAlert tone="warning">{error}</SettingsAlert> : null}

      <section className="space-y-4">
        <SettingsSectionHeader
          eyebrow="Communication"
          title="Sender identity"
          description="Only provider-verified sender domains can be used for live customer delivery."
        />

        <div className="grid gap-5 xl:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
          <SettingsCard
            title="Add sending domain"
            hint="Verification is checked against your DNS records."
            className="h-fit"
          >
            <form onSubmit={add} className="space-y-4">
              <label className={labelClass}>
                Domain
                <input name="domain" required placeholder="billing.example.com" className={fieldClass} />
              </label>
              <label className={labelClass}>
                From address
                <input name="local_part" required defaultValue="accounts" className={fieldClass} />
                <span className="mt-1 block text-xs font-normal text-ink-4">
                  For example, accounts@billing.example.com
                </span>
              </label>
              <button disabled={busy} className={primaryButtonClass}>
                {busy ? "Working…" : "Register sender domain"}
              </button>
            </form>
          </SettingsCard>

          <div className="space-y-4">
            {domains.length ? (
              domains.map((domain) => (
                <article key={domain.id} className="rounded-xl border border-line bg-panel p-5">
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="min-w-0">
                      <h3 className="truncate text-sm font-semibold text-ink">
                        {domain.local_part}@{domain.domain}
                      </h3>
                      <p className="mt-0.5 text-xs text-ink-4">Customer-facing From address</p>
                    </div>
                    <span
                      className={`ml-auto rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider ${
                        domain.status === "verified"
                          ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                          : "bg-amber-500/10 text-amber-700 dark:text-amber-300"
                      }`}
                    >
                      {domain.status}
                    </span>
                  </div>

                  {domain.dns_records.length ? (
                    <div className="mt-4 space-y-2">
                      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">
                        Required DNS records
                      </p>
                      {domain.dns_records.map((record) => (
                        <dl
                          key={`${record.type}-${record.name}`}
                          className="grid gap-1 overflow-x-auto rounded-lg border border-line-2 bg-surface p-3 font-mono text-xs"
                        >
                          <dt className="text-ink-4">
                            {record.type} · {record.name}
                          </dt>
                          <dd className="break-all text-ink-2">{record.value}</dd>
                        </dl>
                      ))}
                    </div>
                  ) : null}

                  {domain.status !== "verified" ? (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void verify(domain.id)}
                      className={`mt-4 ${secondaryButtonClass}`}
                    >
                      Check verification
                    </button>
                  ) : null}
                </article>
              ))
            ) : (
              <div className="rounded-xl border border-dashed border-line bg-panel px-6 py-12 text-center">
                <p className="text-sm font-medium text-ink-2">No sending domains configured</p>
                <p className="mt-1 text-sm text-ink-4">
                  Add one to send live reminders from your own domain.
                </p>
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

function AutomationPanel({ automation }: { automation: Automation | null }) {
  const healthy = automation?.status === "ready";
  const degraded = automation?.status === "degraded";
  const headline = !automation
    ? "Checking automation…"
    : healthy
      ? "Automation is running on schedule."
      : degraded
        ? "Automation needs attention."
        : "Some automation is delayed.";

  return (
    <section className="overflow-hidden rounded-xl border border-line bg-panel">
      <div className="flex flex-wrap items-start gap-3 border-b border-line px-5 py-4">
        <span
          className={`mt-1 size-2 shrink-0 rounded-full ${
            !automation ? "bg-ink-4" : healthy ? "bg-emerald-500" : degraded ? "bg-rose-500" : "bg-amber-500"
          }`}
        />
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-ink">{headline}</h3>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-ink-3">
            {automation?.summary ?? "Reading the latest recorded recovery and reconciliation runs."}
          </p>
        </div>
      </div>

      {automation ? (
        <div className="grid sm:grid-cols-2">
          {Object.entries(automation.jobs).map(([name, job]) => {
            const needsAttention = job.status === "failed" || job.stale;
            return (
              <div key={name} className="border-b border-line-2 px-5 py-4 last:border-b-0 odd:sm:border-r">
                <div className="flex items-center gap-2">
                  <span className={`size-1.5 shrink-0 rounded-full ${needsAttention ? "bg-rose-500" : "bg-emerald-500"}`} />
                  <h4 className="truncate text-xs font-semibold text-ink">
                    {JOB_LABELS[name] ?? name.replaceAll("_", " ")}
                  </h4>
                  <time className="ml-auto shrink-0 text-[11px] tabular-nums text-ink-4">
                    {relativeTime(job.last_started_at)}
                  </time>
                </div>
                <p className="mt-1.5 text-xs text-ink-3">
                  {job.status === "never_run"
                    ? "Waiting for the first recorded run."
                    : job.stale
                      ? "The latest recorded run is delayed."
                      : job.status === "failed"
                        ? "The latest run failed and will remain visible."
                        : "Running on schedule."}
                </p>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="px-5 py-8 text-center text-sm text-ink-4">Loading the latest automation activity…</p>
      )}
    </section>
  );
}
