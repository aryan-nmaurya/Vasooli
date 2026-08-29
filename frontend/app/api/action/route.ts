/**
 * Server-side proxy for operator actions.
 *
 * The browser's backend-issued session remains httpOnly. This route forwards it to
 * the backend, which re-checks that the named account is active and allowed to write.
 *
 * Only the paths below are reachable. Without an allowlist this becomes an open proxy.
 */

import { NextResponse } from "next/server";

import { currentSessionToken } from "@/lib/session";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

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
  // Verify locally for a fast rejection; the backend verifies again authoritatively.
  const sessionToken = await currentSessionToken();
  if (!sessionToken) {
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
      Cookie: `vasooli_session=${sessionToken}`,
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
