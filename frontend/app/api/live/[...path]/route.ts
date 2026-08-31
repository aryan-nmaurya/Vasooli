/**
 * Same-origin passthrough for the live (multi-tenant) API.
 *
 * The demo has always been proxied server-side; the live layer was not — it called
 * the backend straight from the page. That made every live request subject to CORS,
 * and CORS is configuration that is wrong by default: a browser treats
 * `127.0.0.1:3000` and `localhost:3000` as different origins, an apex and a `www` are
 * different origins, and a deployment whose `CORS_ORIGINS` still holds a placeholder
 * rejects all of them. The failure surfaces as an opaque "Failed to fetch" with
 * nothing in it to act on — and it would have taken down sign-in, registration and
 * every live page in production while the demo carried on working.
 *
 * Routing through here removes that dependency. The browser only ever talks to its
 * own origin, and the backend is reached server-to-server where CORS does not apply.
 *
 * The mount path matters and is not incidental. Live auth sets its refresh cookie
 * with `path=/api/live/auth`, so proxying at `/api/live/**` keeps the cookie path
 * identical to the backend's — the browser scopes it exactly as intended, and refresh
 * keeps working. Mounting anywhere else would silently break token rotation.
 */

import { NextResponse } from "next/server";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

/** Headers worth carrying to the backend. Everything else is hop-by-hop or ours. */
const FORWARD_REQUEST_HEADERS = [
  "content-type",
  "cookie",
  "x-merchant-id",
  "x-reauth-token",
  "accept",
];

async function forward(request: Request, path: string[]) {
  const joined = path.join("/");
  const query = new URL(request.url).search;

  const headers = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  // GET and HEAD must not carry one, and Node throws rather than ignoring it.
  const method = request.method.toUpperCase();
  // Keep multipart uploads byte-for-byte intact. Converting a CSV multipart body to
  // text can corrupt arbitrary bytes and makes the original Content-Length invalid.
  const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();

  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE}/api/live/${joined}${query}`, {
      method,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    // The backend host can contain private service-discovery names or infrastructure
    // details. Operators get the target from server logs/config; clients only need an
    // actionable, non-sensitive failure.
    return NextResponse.json(
      { detail: "The Vasooli API is temporarily unavailable. Please try again." },
      { status: 502 },
    );
  }

  const response = new NextResponse(await upstream.arrayBuffer(), {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") ?? "application/json",
      "Cache-Control": "no-store",
    },
  });

  // Session cookies are the whole point of the live auth routes, and a single
  // combined header would corrupt multi-cookie responses — `getSetCookie` keeps them
  // separate.
  for (const cookie of upstream.headers.getSetCookie()) {
    response.headers.append("set-cookie", cookie);
  }
  return response;
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(request: Request, ctx: Ctx) {
  return forward(request, (await ctx.params).path);
}
export async function POST(request: Request, ctx: Ctx) {
  return forward(request, (await ctx.params).path);
}
export async function PUT(request: Request, ctx: Ctx) {
  return forward(request, (await ctx.params).path);
}
export async function PATCH(request: Request, ctx: Ctx) {
  return forward(request, (await ctx.params).path);
}
export async function DELETE(request: Request, ctx: Ctx) {
  return forward(request, (await ctx.params).path);
}
