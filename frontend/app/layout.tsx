import type { Metadata } from "next";

import { AppShell } from "@/components/AppShell";
import { currentSession } from "@/lib/session";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vasooli — Automated B2B Receivables Recovery",
  description: "Connect receivables, run policy-controlled recovery, reconcile payments, and keep every decision auditable.",
};

function themeScript(guidedSignedIn: boolean) {
  return `
(function(){
  var path = location.pathname === '/' ? '/' : location.pathname.replace(/\\/+$/, '');
  var demoRoute = path === '/' || path === '/recovered' || path.startsWith('/recovered/') || path === '/promises' || path.startsWith('/promises/') || path === '/audit' || path.startsWith('/audit/') || path === '/invoices' || path.startsWith('/invoices/') || path === '/settings' || path.startsWith('/settings/');
  if (!${guidedSignedIn} || !demoRoute) {
    document.documentElement.setAttribute('data-theme', 'dark');
    return;
  }
  try {
    var saved = localStorage.getItem('vasooli-theme');
    var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.setAttribute('data-theme', saved || (prefersDark ? 'dark' : 'light'));
  } catch (e) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();
`;
}

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
