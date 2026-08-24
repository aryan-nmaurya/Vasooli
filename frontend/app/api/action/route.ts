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

import { currentSession } from "@/lib/session";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const ADMIN_KEY = process.env.ADMIN_API_KEY ?? "local-dev-key";

const ALLOWED = [
  /^\/api\/admin\/run-cycle(\?.*)?$/,
  /^\/api\/invoices\/[0-9a-f-]{36}\/simulate-reply$/,
  /^\/api\/invoices\/[0-9a-f-]{36}\/provision$/,
  /^\/api\/dashboard\/invoices\/[0-9a-f-]{36}\/escalate$/,
  /^\/api\/dashboard\/invoices\/[0-9a-f-]{36}\/write-off$/,
  /^\/api\/dashboard\/disputes\/[0-9a-f-]{36}\/resolve$/,
  // Operator retries. The event id is provider-supplied, so it is constrained to a
  // conservative character set rather than matched with `.*` — an allowlist entry
  // that accepts anything is not an allowlist.
  /^\/api\/dashboard\/exceptions\/events\/[A-Za-z0-9_.:-]{1,128}\/retry$/,
  /^\/api\/dashboard\/exceptions\/reminders\/[0-9a-f-]{36}\/retry$/,
  /^\/api\/dashboard\/exceptions\/links\/[0-9a-f-]{36}\/retry-closure$/,
];

export async function POST(request: Request) {
  // Session first, before the admin key is attached to anything. Without this the
  // route is an anonymous, credentialed proxy to every operational endpoint —
  // strictly worse than having no proxy at all.
  const operator = await currentSession();
  if (!operator) {
    return NextResponse.json({ error: "Not signed in" }, { status: 401 });
  }

  const { path, body } = (await request.json()) as { path: string; body?: unknown };

  if (!ALLOWED.some((re) => re.test(path))) {
    return NextResponse.json({ error: `path not allowed: ${path}` }, { status: 400 });
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Key": ADMIN_KEY,
      // Who is signed in, for the audit trail only. Every dashboard action arrives
      // at the backend under the same admin key, so without this the audit log
      // attributes every human decision to "service" — which is no attribution at
      // all. It grants nothing: the admin key is what authorises the call, and this
      // is read only to name the person who made it.
      "X-Operator": operator,
    },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });

  const text = await res.text();
  return new NextResponse(text, {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
