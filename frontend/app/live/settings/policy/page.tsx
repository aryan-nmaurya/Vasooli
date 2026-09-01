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
} from "@/components/LiveSettingsSection";
import { liveGet, livePut } from "@/lib/live-api";

type Policy = {
  tier_offsets: number[];
  cooldown_days: number;
  max_attempts: number;
  timezone: string;
  version: number;
};

export default function LivePolicyPage() {
  const [merchant, setMerchant] = useState("");
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const value = window.localStorage.getItem("vasooli_live_merchant") || "";
    Promise.resolve().then(() => setMerchant(value));
    if (value) {
      liveGet<Policy | null>("/api/live/controls/policy", value)
        .then(setPolicy)
        .catch((cause) => setError(cause instanceof Error ? cause.message : "Unable to load policy"));
    }
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await livePut<Policy>("/api/live/controls/policy", merchant, {
        preset: String(data.get("preset")),
        timezone: String(data.get("timezone")),
      });
      setPolicy(result);
      setMessage(`Policy v${result.version} saved.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Policy save failed");
    } finally {
      setBusy(false);
    }
  }

  if (!merchant) return <LiveSignInPrompt what="Your collection policy" />;

  return (
    <div className="space-y-5">
      <SettingsSectionHeader
        eyebrow="Recovery"
        title="Recovery policy"
        description="Offsets are absolute days overdue. Save-time validation rejects schedules that cannot fire."
      />

      {policy ? (
        <div className="rounded-xl border border-line bg-panel-2/60 px-5 py-4">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <p className="text-sm font-semibold text-ink">Active policy</p>
            <span className="rounded-full border border-line bg-surface px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-ink-3">
              v{policy.version}
            </span>
          </div>
          <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-xs text-ink-4">Reminder days</dt>
              <dd className="mt-0.5 font-medium tabular-nums text-ink">{policy.tier_offsets.join(" / ")}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-4">Cooldown</dt>
              <dd className="mt-0.5 font-medium tabular-nums text-ink">{policy.cooldown_days} days</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-4">Max attempts</dt>
              <dd className="mt-0.5 font-medium tabular-nums text-ink">{policy.max_attempts}</dd>
            </div>
          </dl>
        </div>
      ) : null}

      <SettingsCard
        title="Update policy"
        hint="Saving creates a new version. Previous versions stay in the audit log."
        className="max-w-xl"
      >
        <form onSubmit={save} className="space-y-4">
          <label className={labelClass}>
            Preset
            <select name="preset" defaultValue="default" className={fieldClass}>
              <option value="default">Default — 3 / 10 / 21, cooldown 7</option>
              <option value="3_7_14">Fast — 3 / 7 / 14, cooldown 4</option>
            </select>
          </label>
          <label className={labelClass}>
            Timezone
            <input name="timezone" defaultValue={policy?.timezone || "Asia/Kolkata"} className={fieldClass} />
            <span className="mt-1 block text-xs font-normal text-ink-4">
              Reminders are scheduled against this timezone.
            </span>
          </label>
          <button disabled={busy} className={primaryButtonClass}>
            {busy ? "Saving…" : "Save new policy version"}
          </button>
        </form>
      </SettingsCard>

      {message ? <SettingsAlert tone="success">{message}</SettingsAlert> : null}
      {error ? <SettingsAlert tone="error">{error}</SettingsAlert> : null}
    </div>
  );
}
