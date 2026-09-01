"use client";

import { useRouter } from "next/navigation";

import { ExitGuard } from "@/components/ExitGuard";

/** Guards Back out of the guided demo dashboard. Wording matches `SignOutButton`. */
export function DemoExitGuard() {
  const router = useRouter();

  return (
    <ExitGuard
      title="Sign out of Vasooli?"
      body="Your dashboard session will end on this device."
      onConfirm={async () => {
        try {
          await fetch("/api/auth", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "logout" }),
          });
        } catch {
          /* The landing page is the right destination either way. */
        }
        router.replace("/");
        router.refresh();
      }}
    />
  );
}
