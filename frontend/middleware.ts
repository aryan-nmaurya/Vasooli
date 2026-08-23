/**
 * Route guard — convenience only.
 *
 * Sends anonymous visitors to /login instead of a page that will fail to load. It
 * deliberately does NOT verify the token signature: middleware runs on the Edge
 * runtime, where Node's crypto is unavailable, and duplicating the verification with
 * a second implementation would be one more thing to get subtly wrong.
 *
 * The real checks are in the route handlers (`lib/session.verifyToken`) and in the
 * backend, both of which reject an unsigned or forged cookie. A forged cookie gets
 * past this redirect and then fails at every endpoint that matters.
 */

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const SESSION_COOKIE = "vasooli_dash";

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const isPublic =
    pathname === "/login" ||
    pathname.startsWith("/api/auth") ||
    pathname.startsWith("/_next") ||
    pathname === "/favicon.ico";

  if (isPublic) return NextResponse.next();

  if (!request.cookies.get(SESSION_COOKIE)?.value) {
    // API routes get a 401 rather than an HTML redirect, so a fetch sees a status it
    // can act on instead of a login page body.
    if (pathname.startsWith("/api/")) {
      return NextResponse.json({ error: "Not signed in" }, { status: 401 });
    }
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
