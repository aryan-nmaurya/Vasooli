/**
 * Dashboard login and logout.
 *
 * The named operator account and password are verified by the BACKEND, not here.
 *
 * That matters for more than tidiness: this route is what a browser actually posts to,
 * so a password check performed locally means the backend's rate limiter never sees a
 * single login attempt — the endpoint it protects is one nobody uses. Forwarding puts
 * every attempt through the authoritative limiter and leaves one place that knows what
 * the password is.
 *
 * A local limiter runs first as well, to reject the cheap case without a round trip
 * and to keep some protection if the backend is unreachable. Its limitations are
 * documented in lib/rate-limit.ts.
 */

import { NextResponse } from "next/server";

import { clientKey, rateLimit } from "@/lib/rate-limit";
import { SESSION_COOKIE, verifyToken } from "@/lib/session";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

//: Deliberately tight. A person typing a password needs a handful of attempts; a
//: script guessing needs thousands.
const LOGIN_LIMIT = { limit: 8, windowSeconds: 60 };

const COOKIE_OPTIONS = {
  httpOnly: true,
  sameSite: "lax",
  secure: process.env.NODE_ENV === "production",
  path: "/",
  maxAge: 12 * 60 * 60,
} as const;

/**
 * Which sign-in routes this deployment offers.
 *
 * Public, and it carries no secret — it says only whether the reviewer button should
 * render. The login page cannot ask a gated endpoint this, and a button that can only
 * fail is worse than no button.
 */
export async function GET() {
  try {
    const upstream = await fetch(`${API_BASE}/api/auth/modes`, { cache: "no-store" });
    if (!upstream.ok) return NextResponse.json({ reviewer_access: false });
    return NextResponse.json(await upstream.json());
  } catch {
    return NextResponse.json({ reviewer_access: false });
  }
}

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as {
    password?: string;
    username?: string;
    action?: string;
    reviewer?: boolean;
  };

  if (body.action === "logout") {
    const res = NextResponse.json({ status: "ok" });
    res.cookies.delete(SESSION_COOKIE);
    return res;
  }

  if (body.reviewer === true) {
    return issueReviewerSession(request);
  }

  const limit = rateLimit(`login:${clientKey(request)}`, LOGIN_LIMIT);
  if (!limit.allowed) {
    return NextResponse.json(
      { error: "Too many attempts. Try again shortly." },
      { status: 429, headers: { "Retry-After": String(limit.retryAfterSeconds) } },
    );
  }

  const username = body.username?.toLowerCase() ?? "";
  if (!body.password || !/^[a-z0-9_-]{2,64}$/.test(username)) {
    return NextResponse.json({ error: "Invalid credentials" }, { status: 401 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: body.password, username }),
      cache: "no-store",
    });
  } catch {
    // The backend is unreachable. Refusing to sign in is the only safe answer —
    // falling back to a local check would mean the one place that knows the password
    // is whichever process happens to be up.
    return NextResponse.json(
      { error: "Cannot reach the server. Try again shortly." },
      { status: 503 },
    );
  }

  if (upstream.status === 429) {
    return NextResponse.json(
      { error: "Too many attempts. Try again shortly." },
      { status: 429, headers: { "Retry-After": upstream.headers.get("retry-after") ?? "60" } },
    );
  }

  if (!upstream.ok) {
    // Deliberately vague, and identical for every kind of failure: distinguishing
    // "no such account" from "wrong password" only helps someone guessing.
    return NextResponse.json({ error: "Invalid credentials" }, { status: 401 });
  }

  const identity = (await upstream.json().catch(() => null)) as
    | { username?: string; role?: string }
    | null;
  // Read the backend's httpOnly Set-Cookie server-side. The token is deliberately
  // absent from the JSON body, so browser JavaScript cannot retrieve it by calling
  // the backend login endpoint directly from an allowed origin.
  const upstreamCookie = upstream.headers.get("set-cookie") ?? "";
  const issuedToken = /(?:^|[,;]\s*)vasooli_session=([^;,]+)/.exec(upstreamCookie)?.[1];
  // Trust only the canonical identity returned by the backend. Signing the submitted
  // username would recreate self-asserted attribution even though the password check
  // itself was database-backed.
  if (
    !identity?.username ||
    !/^[a-z0-9_-]{2,64}$/.test(identity.username) ||
    verifyToken(issuedToken) !== identity.username
  ) {
    return NextResponse.json({ error: "Invalid authentication response" }, { status: 502 });
  }

  const res = NextResponse.json({ status: "ok" });
  res.cookies.set(SESSION_COOKIE, issuedToken!, COOKIE_OPTIONS);
  return res;
}


/**
 * Open a read-only session without a credential being sent to anyone.
 *
 * Rate limited on the same basis as login: this issues a real session, and an endpoint
 * that mints sessions without a password is exactly the one worth limiting. Read-only
 * is not promised here — the backend refuses to issue this session for anything but an
 * auditor account, and app.api.deps then refuses every non-GET request it makes.
 */
async function issueReviewerSession(request: Request) {
  const limit = rateLimit(`reviewer:${clientKey(request)}`, LOGIN_LIMIT);
  if (!limit.allowed) {
    return NextResponse.json(
      { error: "Too many attempts. Try again shortly." },
      { status: 429, headers: { "Retry-After": String(limit.retryAfterSeconds) } },
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE}/api/auth/reviewer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      { error: "Cannot reach the server. Try again shortly." },
      { status: 503 },
    );
  }

  if (!upstream.ok) {
    // 404 when reviewer access is off, 503 when the account is missing or is not an
    // auditor. Neither is something a visitor can act on, so both read the same.
    return NextResponse.json({ error: "Reviewer access is not available" }, { status: 403 });
  }

  const identity = (await upstream.json().catch(() => null)) as
    | { username?: string; role?: string }
    | null;
  const upstreamCookie = upstream.headers.get("set-cookie") ?? "";
  const issuedToken = /(?:^|[,;]\s*)vasooli_session=([^;,]+)/.exec(upstreamCookie)?.[1];

  // Same verification as a password login, plus the role. Trusting the backend to have
  // checked is not enough when the check is the entire safety property.
  if (
    !identity?.username ||
    identity.role !== "auditor" ||
    verifyToken(issuedToken) !== identity.username
  ) {
    return NextResponse.json({ error: "Invalid authentication response" }, { status: 502 });
  }

  const res = NextResponse.json({ status: "ok", role: identity.role });
  res.cookies.set(SESSION_COOKIE, issuedToken!, COOKIE_OPTIONS);
  return res;
}
