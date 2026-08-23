/**
 * Dashboard login and logout.
 *
 * The password is checked here, server-side, and exchanged for a signed httpOnly
 * cookie. It is never stored by the browser and never sent again.
 */

import { NextResponse } from "next/server";

import { SESSION_COOKIE, checkPassword, createToken } from "@/lib/session";

export async function POST(request: Request) {
  const { password, action } = (await request.json()) as {
    password?: string;
    action?: string;
  };

  if (action === "logout") {
    const res = NextResponse.json({ status: "ok" });
    res.cookies.delete(SESSION_COOKIE);
    return res;
  }

  if (!password || !checkPassword(password)) {
    // Deliberately vague: distinguishing "no such user" from "wrong password" only
    // helps someone guessing.
    return NextResponse.json({ error: "Invalid password" }, { status: 401 });
  }

  const res = NextResponse.json({ status: "ok" });
  res.cookies.set(SESSION_COOKIE, createToken(), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 12 * 60 * 60,
  });
  return res;
}
