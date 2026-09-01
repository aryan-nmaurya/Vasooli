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
} from "@/components/LiveSettingsSection";
import { liveDelete, liveGet, livePost } from "@/lib/live-api";

type Role = { id: string; slug: string; name: string; description: string };
type Member = { id: string; email: string; display_name: string | null; role: string; active: boolean };
type Invitation = { id: string; email: string; role: string | null; expires_at: string };
type Team = { roles: Role[]; members: Member[]; invitations: Invitation[] };

export default function LiveTeamPage() {
  const [merchant, setMerchant] = useState("");
  const [team, setTeam] = useState<Team | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const load = useCallback(async (id: string) => setTeam(await liveGet<Team>("/api/live/team", id)), []);

  useEffect(() => {
    const id = window.localStorage.getItem("vasooli_live_merchant") || "";
    Promise.resolve().then(async () => {
      setMerchant(id);
      if (id) {
        await load(id).catch((cause) =>
          setError(cause instanceof Error ? cause.message : "Unable to load team"),
        );
      }
    });
  }, [load]);

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!merchant) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await livePost<{ invitation_token?: string | null }>(
        "/api/live/team/invitations",
        merchant,
        { email: String(data.get("email")), role_id: String(data.get("role_id")) },
      );
      setMessage(
        result.invitation_token
          ? `Invitation created. Local invitation token: ${result.invitation_token}`
          : "Invitation sent.",
      );
      form.reset();
      await load(merchant);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Invitation failed");
    } finally {
      setBusy(false);
    }
  }

  async function remove(path: string) {
    setBusy(true);
    setError(null);
    try {
      await liveDelete(path, merchant);
      await load(merchant);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Access update failed");
    } finally {
      setBusy(false);
    }
  }

  if (!merchant) return <LiveSignInPrompt what="Your team" />;

  return (
    <div className="space-y-5">
      <SettingsSectionHeader
        eyebrow="Manage"
        title="Team access"
        description="Invite teammates with least-privilege roles. Seat limits and permissions are enforced by the server."
      />

      {team ? (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
          <SettingsCard title="Invite a teammate" hint="Invitations expire after seven days." className="h-fit">
            <form onSubmit={invite} className="space-y-4">
              <label className={labelClass}>
                Work email
                <input name="email" type="email" required className={fieldClass} />
              </label>
              <label className={labelClass}>
                Role
                <select name="role_id" required className={fieldClass}>
                  {team.roles.map((role) => (
                    <option key={role.id} value={role.id}>
                      {role.name} — {role.description}
                    </option>
                  ))}
                </select>
              </label>
              <button disabled={busy || !team.roles.length} className={primaryButtonClass}>
                {busy ? "Working…" : "Send invitation"}
              </button>
            </form>
          </SettingsCard>

          <div className="space-y-5">
            <section className="overflow-hidden rounded-xl border border-line bg-panel">
              <div className="flex items-center gap-2 border-b border-line px-5 py-4">
                <h3 className="text-sm font-semibold text-ink">Members</h3>
                <span className="rounded-full bg-panel-2 px-2 py-0.5 text-[10px] font-semibold tabular-nums text-ink-3">
                  {team.members.length}
                </span>
              </div>
              {team.members.length ? (
                team.members.map((member) => (
                  <div
                    key={member.id}
                    className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3 text-sm last:border-0"
                  >
                    <div className="min-w-0">
                      <p className="truncate font-medium text-ink">{member.display_name || member.email}</p>
                      {member.display_name ? (
                        <p className="truncate text-xs text-ink-4">{member.email}</p>
                      ) : null}
                    </div>
                    <span className="ml-auto rounded-full bg-panel-2 px-2.5 py-1 text-xs capitalize text-ink-3">
                      {member.role}
                    </span>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void remove(`/api/live/team/members/${member.id}`)}
                      className="rounded-md px-2 py-1 text-xs font-medium text-rose-700 transition hover:bg-rose-500/10 disabled:opacity-40 dark:text-rose-300"
                    >
                      Revoke
                    </button>
                  </div>
                ))
              ) : (
                <p className="px-5 py-5 text-sm text-ink-4">No members yet.</p>
              )}
            </section>

            <section className="overflow-hidden rounded-xl border border-line bg-panel">
              <div className="border-b border-line px-5 py-4">
                <h3 className="text-sm font-semibold text-ink">Pending invitations</h3>
              </div>
              {team.invitations.length ? (
                team.invitations.map((invitation) => (
                  <div
                    key={invitation.id}
                    className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3 text-sm last:border-0"
                  >
                    <div className="min-w-0">
                      <p className="truncate font-medium text-ink">{invitation.email}</p>
                      <p className="text-xs text-ink-4">
                        Expires {new Date(invitation.expires_at).toLocaleDateString()}
                      </p>
                    </div>
                    <span className="ml-auto text-xs capitalize text-ink-3">{invitation.role}</span>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void remove(`/api/live/team/invitations/${invitation.id}`)}
                      className="rounded-md px-2 py-1 text-xs font-medium text-rose-700 transition hover:bg-rose-500/10 disabled:opacity-40 dark:text-rose-300"
                    >
                      Revoke
                    </button>
                  </div>
                ))
              ) : (
                <p className="px-5 py-5 text-sm text-ink-4">No pending invitations.</p>
              )}
            </section>
          </div>
        </div>
      ) : !error ? (
        <p className="text-sm text-ink-4">Loading team…</p>
      ) : null}

      {message ? <SettingsAlert tone="success">{message}</SettingsAlert> : null}
      {error ? <SettingsAlert tone="error">{error}</SettingsAlert> : null}
    </div>
  );
}
