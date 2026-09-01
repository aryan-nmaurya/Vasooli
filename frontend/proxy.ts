/**
 * Route guard — convenience only.
 *
 * Sends anonymous visitors to /login instead of a page that will fail to load. It
 * deliberately does NOT verify the token signature: Proxy is only an optimistic
 * navigation guard, and duplicating the verification with
 * a second implementation would be one more thing to get subtly wrong.
 *
 * The real checks are in the route handlers (`lib/session.verifyToken`) and in the
 * backend, both of which reject an unsigned or forged cookie. A forged cookie gets
 * past this redirect and then fails at every endpoint that matters.
 */

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const SESSION_COOKIE = "vasooli_dash";

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const isPublic =
    pathname === "/login" ||
    pathname === "/register" ||
    pathname === "/pricing" ||
    pathname === "/privacy" ||
    pathname === "/terms" ||
    pathname === "/dpa" ||
    pathname === "/verify-email" ||
    pathname === "/forgot-password" ||
    pathname === "/reset-password" ||
    pathname.startsWith("/live") ||
    // The root renders a public landing page for anonymous visitors and the
    // dashboard for signed-in ones — the branch is in app/page.tsx, not here.
    // Gating it in the proxy would send a cold visitor to a password field, which
    // is exactly the dead end this replaced.
    pathname === "/" ||
    // The reviewer guide is deliberately reachable without a session: someone sent
    // this link cold should learn what the product is before being asked for a
    // password. It carries no data and no credentials.
    pathname === "/guide" ||
    pathname.startsWith("/demo/") ||
    pathname.startsWith("/api/auth") ||
    // The live API carries its own session (`vasooli_live_access`), issued by the
    // backend and unrelated to the demo cookie this guard checks. Gating it here
    // would demand a demo session from a live merchant who has no reason to hold
    // one — and would make signing in impossible, since sign-in is itself a call to
    // this prefix. As the note above says, this guard is convenience: the backend
    // authorises every /api/live route on its own, requiring an active session, an
    // explicit X-Merchant-ID, and a membership behind it.
    pathname.startsWith("/api/live") ||
    pathname.startsWith("/_next") ||
    pathname === "/vasooli-logo.png" ||
    pathname === "/vasooli-favicon-rounded.png" ||
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
