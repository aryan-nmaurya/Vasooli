/**
 * Authenticated read proxy.
 *
 * Client components poll through here rather than calling the backend directly, for
 * two reasons: the browser never needs a backend credential, and the backend is not
 * exposed to the public internet as a readable API.
 *
 * Every request is checked against the Next session cookie BEFORE the admin key is
 * attached. Without that check this route would be an open, credentialed proxy — a
 * worse hole than the one it closes.
 */

import { NextResponse } from "next/server";

import { currentSession } from "@/lib/session";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const ADMIN_KEY = process.env.ADMIN_API_KEY ?? "";

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
];

export async function GET(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  if (!(await currentSession())) {
    return NextResponse.json({ error: "Not signed in" }, { status: 401 });
  }

  const { path } = await ctx.params;
  const joined = path.join("/");
  if (!ALLOWED.some((re) => re.test(joined))) {
    return NextResponse.json({ error: `path not allowed: ${joined}` }, { status: 400 });
  }

  const query = new URL(request.url).search;
  const res = await fetch(`${API_BASE}/api/${joined}${query}`, {
    headers: { "X-Admin-Key": ADMIN_KEY },
    cache: "no-store",
  });

  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
