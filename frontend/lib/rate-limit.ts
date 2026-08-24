/**
 * In-process rate limiting for the Next.js server.
 *
 * **Known limitation, stated plainly:** this counter lives in the memory of one
 * server instance. On Vercel, requests may be served by several instances, and a
 * cold start resets the window — so an attacker distributing attempts across
 * instances gets more than `limit` tries.
 *
 * That is acceptable because it is not the only defence. The login route forwards
 * every attempt to the backend, whose limiter is authoritative and runs in a single
 * long-lived process. This layer exists to stop the cheap case — a script hammering
 * one endpoint — without a round trip, and to keep working if the backend is down.
 *
 * Redis would make it exact. For one small deployment that is more infrastructure to
 * run, secure and explain than the remaining risk justifies.
 */

type Window = { hits: number[] };

const windows = new Map<string, Window>();

export type RateLimitResult = {
  allowed: boolean;
  retryAfterSeconds: number;
};

export function rateLimit(
  key: string,
  { limit, windowSeconds }: { limit: number; windowSeconds: number },
): RateLimitResult {
  const now = Date.now();
  const cutoff = now - windowSeconds * 1000;

  const entry = windows.get(key) ?? { hits: [] };
  entry.hits = entry.hits.filter((t) => t > cutoff);

  if (entry.hits.length >= limit) {
    windows.set(key, entry);
    const oldest = entry.hits[0];
    return {
      allowed: false,
      retryAfterSeconds: Math.max(1, Math.ceil((oldest + windowSeconds * 1000 - now) / 1000)),
    };
  }

  entry.hits.push(now);
  windows.set(key, entry);

  // Bound the map so a spray of distinct keys cannot grow it without limit.
  if (windows.size > 5000) {
    for (const [k, v] of windows) {
      if (v.hits.every((t) => t <= cutoff)) windows.delete(k);
    }
  }

  return { allowed: true, retryAfterSeconds: 0 };
}

/**
 * Best-effort client identity.
 *
 * Vercel and most proxies set x-forwarded-for. It is spoofable, so this is a speed
 * bump rather than a security boundary — the boundary is the password itself and the
 * signed session cookie.
 */
export function clientKey(request: Request): string {
  const forwarded = request.headers.get("x-forwarded-for");
  if (forwarded) return forwarded.split(",")[0].trim();
  return request.headers.get("x-real-ip") ?? "unknown";
}

/** Test-only: clear counters between cases. */
export function _resetRateLimits(): void {
  windows.clear();
}
