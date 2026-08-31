"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { LiveSignInPrompt } from "@/components/LiveSignInPrompt";
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
      if (id) await load(id).catch((cause) => setError(cause instanceof Error ? cause.message : "Unable to load team"));
    });
  }, [load]);

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!merchant) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true); setError(null); setMessage(null);
    try {
      const result = await livePost<{ invitation_token?: string | null }>("/api/live/team/invitations", merchant, { email: String(data.get("email")), role_id: String(data.get("role_id")) });
      setMessage(result.invitation_token ? `Invitation created. Local invitation token: ${result.invitation_token}` : "Invitation sent.");
      form.reset(); await load(merchant);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Invitation failed"); }
    finally { setBusy(false); }
  }

  async function remove(path: string) {
    setBusy(true); setError(null);
    try { await liveDelete(path, merchant); await load(merchant); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Access update failed"); }
    finally { setBusy(false); }
  }

  return <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
    <h1 className="text-3xl font-semibold">Team access</h1><p className="mt-2 max-w-2xl text-ink-3">Invite teammates with least-privilege roles. Seat limits and permissions are enforced by the server.</p>
    {!merchant ? <p className="mt-6 rounded-xl border border-line bg-panel p-4 text-sm text-ink-3">Sign in to a live workspace to manage its team.</p> : null}
    {team ? <div className="mt-7 grid gap-5 lg:grid-cols-[0.8fr_1.2fr]">
      <form onSubmit={invite} className="h-fit space-y-4 rounded-2xl border border-line bg-panel p-5"><div><h2 className="font-semibold">Invite a teammate</h2><p className="mt-1 text-xs text-ink-4">Invitations expire after seven days.</p></div><label className="block text-sm">Work email<input name="email" type="email" required className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label><label className="block text-sm">Role<select name="role_id" required className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2">{team.roles.map((role) => <option key={role.id} value={role.id}>{role.name} — {role.description}</option>)}</select></label><button disabled={busy || !team.roles.length} className="rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">{busy ? "Working…" : "Send invitation"}</button></form>
      <div className="space-y-5"><section className="overflow-hidden rounded-2xl border border-line bg-panel"><div className="border-b border-line px-5 py-4"><h2 className="font-semibold">Members</h2></div>{team.members.map((member) => <div key={member.id} className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3 text-sm last:border-0"><div><p className="font-medium">{member.display_name || member.email}</p>{member.display_name ? <p className="text-xs text-ink-4">{member.email}</p> : null}</div><span className="ml-auto rounded-full bg-panel-2 px-2.5 py-1 text-xs capitalize text-ink-3">{member.role}</span><button type="button" disabled={busy} onClick={() => void remove(`/api/live/team/members/${member.id}`)} className="text-xs text-rose-700 hover:underline disabled:opacity-40">Revoke</button></div>)}</section>
      <section className="overflow-hidden rounded-2xl border border-line bg-panel"><div className="border-b border-line px-5 py-4"><h2 className="font-semibold">Pending invitations</h2></div>{team.invitations.length ? team.invitations.map((invite) => <div key={invite.id} className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3 text-sm last:border-0"><div><p className="font-medium">{invite.email}</p><p className="text-xs text-ink-4">Expires {new Date(invite.expires_at).toLocaleDateString()}</p></div><span className="ml-auto text-xs capitalize text-ink-3">{invite.role}</span><button type="button" disabled={busy} onClick={() => void remove(`/api/live/team/invitations/${invite.id}`)} className="text-xs text-rose-700 hover:underline disabled:opacity-40">Revoke</button></div>) : <p className="px-5 py-5 text-sm text-ink-4">No pending invitations.</p>}</section></div>
    </div> : !merchant ? <LiveSignInPrompt what="Your team" /> : !error ? <p className="mt-7 text-sm text-ink-4">Loading team…</p> : null}
    {message ? <p className="mt-5 break-words rounded-lg bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700">{message}</p> : null}{error ? <p role="alert" className="mt-5 rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">{error}</p> : null}
  </main>;
}
