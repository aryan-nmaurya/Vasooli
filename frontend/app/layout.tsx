import type { Metadata } from "next";

import { AppShell } from "@/components/AppShell";
import { currentSession } from "@/lib/session";
import { themeScript } from "@/lib/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vasooli — Automated B2B Receivables Recovery",
  description: "Connect receivables, run policy-controlled recovery, reconcile payments, and keep every decision auditable.",
  icons: {
    icon: [{ url: "/vasooli-favicon-rounded.png", type: "image/png" }],
    shortcut: "/vasooli-favicon-rounded.png",
    apple: "/vasooli-favicon-rounded.png",
  },
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const signedIn = Boolean(await currentSession());

  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript(signedIn) }} />
      </head>
      <body className="min-h-screen bg-surface text-ink antialiased">
        <AppShell guidedSignedIn={signedIn}>{children}</AppShell>
      </body>
    </html>
  );
}
