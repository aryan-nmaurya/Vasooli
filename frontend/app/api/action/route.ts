/**
 * Server-side proxy for admin actions.
 *
 * The admin key lives in ADMIN_API_KEY (no NEXT_PUBLIC_ prefix), so it stays on the
 * server and never reaches the browser bundle. The dashboard calls this route; this
 * route calls the backend with the key attached.
 *
 * Only the paths below are reachable. Without an allowlist this becomes an open proxy
 * that will attach the admin key to any URL a caller names.
 */

import { NextResponse } from "next/server";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const ADMIN_KEY = process.env.ADMIN_API_KEY ?? "local-dev-key";

const ALLOWED = [
  /^\/api\/admin\/run-cycle(\?.*)?$/,
  /^\/api\/invoices\/[0-9a-f-]{36}\/simulate-reply$/,
  /^\/api\/invoices\/[0-9a-f-]{36}\/provision$/,
  /^\/api\/dashboard\/invoices\/[0-9a-f-]{36}\/escalate$/,
  /^\/api\/dashboard\/invoices\/[0-9a-f-]{36}\/write-off$/,
];

export async function POST(request: Request) {
  const { path, body } = (await request.json()) as { path: string; body?: unknown };

  if (!ALLOWED.some((re) => re.test(path))) {
    return NextResponse.json({ error: `path not allowed: ${path}` }, { status: 400 });
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Admin-Key": ADMIN_KEY },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });

  const text = await res.text();
  return new NextResponse(text, {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
