/**
 * Authenticated file download proxy.
 *
 * Separate from /api/proxy because that one reads the upstream body as text and
 * stamps every response `application/json` — which is right for the dashboard's data
 * and silently corrupts an .xlsx or .pdf. This forwards the raw bytes and preserves
 * the upstream Content-Type and Content-Disposition so the browser saves the file
 * under the name the backend chose.
 *
 * Read-only, GET-only, and allowlisted the same way as the JSON proxy: an export
 * endpoint that accepts an arbitrary path is a data-exfiltration route with a
 * friendly name.
 */

import { NextResponse } from "next/server";

import { currentSessionToken } from "@/lib/session";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

const ALLOWED = [/^export\/(recovered|overview|invoices)$/, /^invoices\/import\/template$/];

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

  // Only known parameters travel onward. Forwarding the whole query string would
  // let a caller append ones the backend never intended to accept here.
  const incoming = new URL(request.url).searchParams;
  const format = incoming.get("format") ?? "csv";
  if (!["csv", "xlsx", "pdf"].includes(format)) {
    return NextResponse.json({ error: "unsupported format" }, { status: 400 });
  }

  const outgoing = new URLSearchParams({ format });
  // The dashboard's two filters, so a download matches what is on screen. Shape-checked
  // here as well as on the backend — these end up in a WHERE clause.
  for (const key of ["status", "reason"] as const) {
    const value = incoming.get(key);
    if (value && /^[a-z_]{1,32}$/.test(value)) outgoing.set(key, value);
  }

  const upstream = await fetch(`${API_BASE}/api/${joined}?${outgoing}`, {
    headers: { Cookie: `vasooli_session=${sessionToken}` },
    cache: "no-store",
  });

  if (!upstream.ok) {
    return NextResponse.json(
      { error: "Export failed" },
      { status: upstream.status === 401 ? 401 : 502 },
    );
  }

  return new NextResponse(await upstream.arrayBuffer(), {
    status: 200,
    headers: {
      "Content-Type": upstream.headers.get("content-type") ?? "application/octet-stream",
      "Content-Disposition":
        upstream.headers.get("content-disposition") ?? `attachment; filename="export.${format}"`,
      "Cache-Control": "no-store",
    },
  });
}
