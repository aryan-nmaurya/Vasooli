/**
 * Dashboard login and logout.
 *
 * The password is verified by the BACKEND, not here.
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
import { SESSION_COOKIE, createToken } from "@/lib/session";

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

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as {
    password?: string;
    action?: string;
  };

  if (body.action === "logout") {
    const res = NextResponse.json({ status: "ok" });
    res.cookies.delete(SESSION_COOKIE);
    return res;
  }

  const limit = rateLimit(`login:${clientKey(request)}`, LOGIN_LIMIT);
  if (!limit.allowed) {
    return NextResponse.json(
      { error: "Too many attempts. Try again shortly." },
      { status: 429, headers: { "Retry-After": String(limit.retryAfterSeconds) } },
    );
  }

  if (!body.password) {
    return NextResponse.json({ error: "Invalid password" }, { status: 401 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: body.password }),
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
    return NextResponse.json({ error: "Invalid password" }, { status: 401 });
  }

  const res = NextResponse.json({ status: "ok" });
  res.cookies.set(SESSION_COOKIE, createToken(), COOKIE_OPTIONS);
  return res;
}
