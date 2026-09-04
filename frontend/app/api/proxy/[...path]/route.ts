/**
 * Authenticated read proxy.
 *
 * Client components poll through here rather than calling the backend directly, for
 * two reasons: the browser never handles a readable credential in JavaScript, and
 * the backend remains the authoritative account/role check.
 *
 * Every request is checked locally, then the backend-issued session is forwarded and
 * verified again against the operator account.
 */

import { NextResponse } from "next/server";

import { currentSessionToken } from "@/lib/session";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

//: Read-only paths. Anything that changes state goes through /api/action, which has
//: its own tighter allowlist.
const ALLOWED = [
  /^dashboard\/overview$/,
  /^dashboard\/queue$/,
  /^dashboard\/promises$/,
  /^dashboard\/disputes$/,
  /^dashboard\/audit$/,
  /^dashboard\/exceptions$/,
  /^dashboard\/invoices\/[0-9a-f-]{36}$/,
  /^invoices$/,
  /^invoices\/[0-9a-f-]{36}$/,
  // The demo shell asks the backend whether the demo controls exist at all, so the
  // Workspace settings entry can be absent on a deployment that has none rather than
  // linking to a page that only says so. Read-only, and the demo router still
  // requires the operator session on top of the check above.
  /^demo\/clock$/,
];

export async function GET(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const sessionToken = await currentSessionToken();
  if (!sessionToken) {
    return NextResponse.json({ error: "Not signed in" }, { status: 401 });
  }

  const { path } = await ctx.params;
  const joined = path.join("/");
  if (!ALLOWED.some((re) => re.test(joined))) {
    return NextResponse.json({ error: `path not allowed: ${joined}` }, { status: 400 });
  }

  const query = new URL(request.url).search;
  const res = await fetch(`${API_BASE}/api/${joined}${query}`, {
    headers: { Cookie: `vasooli_session=${sessionToken}` },
    cache: "no-store",
  });

  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
