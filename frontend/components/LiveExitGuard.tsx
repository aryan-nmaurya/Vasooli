"use client";

import { useRouter } from "next/navigation";

import { ExitGuard } from "@/components/ExitGuard";
import { logoutLive } from "@/lib/live-api";

/** Guards Back out of the live workspace. Wording matches `LiveSignOutButton`. */
export function LiveExitGuard() {
  const router = useRouter();

  return (
    <ExitGuard
      title="Sign out of Vasooli?"
      body="Your live workspace session will end on this device."
      onConfirm={async () => {
        try {
          await logoutLive();
        } catch {
          /* Clear the local workspace selection even if the server session expired. */
        }
        window.localStorage.removeItem("vasooli_live_merchant");
        router.replace("/");
        router.refresh();
      }}
    />
  );
}
