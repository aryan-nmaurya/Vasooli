import type { NextConfig } from "next";

/**
 * Security headers, applied by Next itself.
 *
 * Set here rather than only in vercel.json so they hold in local development and on
 * any host — a header that exists only in production configuration is one nobody
 * notices is broken until production.
 *
 * The CSP is written to be genuinely restrictive without breaking the app, which
 * meant checking what the app actually needs:
 *
  * - script-src: see the isDev split below.
 *   before paint to prevent a flash of the wrong theme. Removing 'unsafe-inline'
 *   would need a nonce threaded through that script; worth doing in production, not
 *   worth breaking the demo over. Documented as a known limitation.
 * - `style-src 'self' 'unsafe-inline'` — Tailwind emits inline styles.
 * - `connect-src` — the dashboard talks to its own origin only. Client-side polling
 *   goes through /api/proxy, so the backend origin is never contacted from a browser
 *   and does not belong here.
 * - `frame-ancestors 'none'` — this dashboard is never embedded.
 */
const isDev = process.env.NODE_ENV !== "production";

/**
 * `unsafe-eval` in DEVELOPMENT ONLY.
 *
 * React's dev build uses eval() for debugging features — reconstructing call stacks
 * across environments, mainly. Without it the dev server throws and the page will not
 * render. React never uses eval() in production, so the production policy stays
 * strict and this relaxation never ships.
 *
 * Verified in both modes rather than assumed: the first version of this file was
 * written and checked against `next build` alone, which is exactly why it broke the
 * dev server and nothing caught it.
 */
const scriptSrc = isDev
  ? "'self' 'unsafe-inline' 'unsafe-eval'"
  : "'self' 'unsafe-inline'";

const CSP = [
  "default-src 'self'",
  `script-src ${scriptSrc}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self' data:",
  // The dev server pushes hot-reload updates over a websocket on the same origin.
  isDev ? "connect-src 'self' ws: wss:" : "connect-src 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "object-src 'none'",
].join("; ");

const SECURITY_HEADERS = [
  { key: "Content-Security-Policy", value: CSP },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
];

/**
 * The workspace settings pages used to sit at the top level of `/live`, reachable
 * only through cards on the settings hub. They now live under `/live/settings/*`
 * behind a shared layout. These redirects keep old links and bookmarks working;
 * they are permanent because the previous paths are not coming back.
 */
const SETTINGS_REDIRECTS = ["integrations", "policy", "billing", "team"].map((section) => ({
  source: `/live/${section}`,
  destination: `/live/settings/${section}`,
  permanent: true,
}));

const nextConfig: NextConfig = {
  async headers() {
    return [{ source: "/:path*", headers: SECURITY_HEADERS }];
  },
  async redirects() {
    return SETTINGS_REDIRECTS;
  },
};

export default nextConfig;
