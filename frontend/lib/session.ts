/**
 * Dashboard session, held server-side.
 *
 * The browser gets an httpOnly cookie containing a signed token and nothing else. The
 * backend's admin key stays in the Next.js server process, so it is never in the
 * client bundle and never in a request the browser can inspect or replay.
 *
 * The signature is HMAC-SHA256 over `subject.expiry`, verified with Node's timing-safe
 * comparison. Same scheme as the backend, so either side can mint a session if that
 * ever becomes useful.
 */

import { createHmac, timingSafeEqual } from "crypto";
import { cookies } from "next/headers";

export const SESSION_COOKIE = "vasooli_dash";
const TTL_SECONDS = 12 * 60 * 60;
const VERSION = "v1";

function secret(): string {
  const value = process.env.SESSION_SECRET;
  if (!value) {
    // Failing loudly beats silently signing every session with "undefined".
    throw new Error("SESSION_SECRET is not set");
  }
  return value;
}

function sign(payload: string): string {
  return createHmac("sha256", secret()).update(payload).digest("base64url");
}

export function createToken(subject = "operator"): string {
  const expires = Math.floor(Date.now() / 1000) + TTL_SECONDS;
  const payload = `${VERSION}.${subject}.${expires}`;
  return `${payload}.${sign(payload)}`;
}

export function verifyToken(token: string | undefined): string | null {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 4) return null;

  const [version, subject, expiresRaw, signature] = parts;
  if (version !== VERSION) return null;

  const expected = Buffer.from(sign(`${version}.${subject}.${expiresRaw}`));
  const given = Buffer.from(signature);
  if (expected.length !== given.length || !timingSafeEqual(expected, given)) return null;

  const expires = Number(expiresRaw);
  if (!Number.isFinite(expires) || expires < Date.now() / 1000) return null;

  return subject;
}

export async function currentSession(): Promise<string | null> {
  const store = await cookies();
  return verifyToken(store.get(SESSION_COOKIE)?.value);
}

export function checkPassword(candidate: string): boolean {
  const expected = process.env.DASHBOARD_PASSWORD ?? "";
  if (!expected) return false;
  const a = Buffer.from(candidate);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}
