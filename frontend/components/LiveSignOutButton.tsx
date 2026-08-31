"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { logoutLive } from "@/lib/live-api";

export function LiveSignOutButton() {
  const router = useRouter();
  const [merchant, setMerchant] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { Promise.resolve().then(() => setMerchant(window.localStorage.getItem("vasooli_live_merchant") || "")); }, []);

  if (!merchant) return <Link href="/live/login" className="rounded-lg border border-line bg-panel px-3 py-2 text-xs text-ink-2 hover:bg-panel-2">Sign in</Link>;

  async function signOut() {
    setBusy(true);
    try { await logoutLive(); } catch { /* Clear local workspace selection even if the server session expired. */ }
    window.localStorage.removeItem("vasooli_live_merchant");
    router.replace("/live/login");
    router.refresh();
  }

  return <button type="button" disabled={busy} onClick={() => void signOut()} className="rounded-lg border border-line bg-panel px-3 py-2 text-xs text-ink-2 hover:bg-panel-2 disabled:opacity-50">{busy ? "Signing out…" : "Sign out"}</button>;
}
