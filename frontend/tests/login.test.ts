/**
 * Frontend login route. P0 security.
 *
 * The gap this closes: this route is what the browser actually posts to, and it used
 * to verify the password locally. The backend's rate limiter therefore protected an
 * endpoint nobody used, while the real login door had no limit at all.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetRateLimits } from "@/lib/rate-limit";

const POST_URL = "http://localhost:3000/api/auth";

function loginRequest(password: string, ip = "203.0.113.7"): Request {
  return new Request(POST_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-forwarded-for": ip },
    body: JSON.stringify({ password }),
  });
}

/** Stands in for the backend's /api/auth/login. */
function mockBackend(status: number, headers: Record<string, string> = {}) {
  // Typed with the fetch signature so `mock.calls[0]` is a real tuple rather than
  // an empty one — otherwise asserting on the URL is a type error.
  return vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify({}), { status, headers }),
  );
}

describe("POST /api/auth", () => {
  beforeEach(() => {
    vi.resetModules();
    _resetRateLimits();
    process.env.SESSION_SECRET = "a-test-secret-long-enough-to-sign-tokens-with";
    process.env.NEXT_PUBLIC_API_URL = "http://backend.test";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("signs in when the backend accepts the password", async () => {
    vi.stubGlobal("fetch", mockBackend(200));
    const { POST } = await import("@/app/api/auth/route");

    const res = await POST(loginRequest("correct"));
    expect(res.status).toBe(200);
  });

  it("sets an httpOnly, SameSite=Lax session cookie", async () => {
    vi.stubGlobal("fetch", mockBackend(200));
    const { POST } = await import("@/app/api/auth/route");

    const cookie = (await POST(loginRequest("correct"))).headers.get("set-cookie") ?? "";
    // httpOnly is what stops an XSS bug exfiltrating the session; SameSite is the
    // CSRF protection for every state-changing action behind it.
    expect(cookie.toLowerCase()).toContain("httponly");
    expect(cookie.toLowerCase()).toContain("samesite=lax");
    expect(cookie).toContain("vasooli_dash=");
  });

  it("forwards the password to the backend rather than checking it locally", async () => {
    const backend = mockBackend(200);
    vi.stubGlobal("fetch", backend);
    const { POST } = await import("@/app/api/auth/route");

    await POST(loginRequest("correct"));

    expect(backend).toHaveBeenCalledOnce();
    expect(String(backend.mock.calls[0][0])).toContain("/api/auth/login");
  });

  it("rejects a wrong password", async () => {
    vi.stubGlobal("fetch", mockBackend(401));
    const { POST } = await import("@/app/api/auth/route");

    const res = await POST(loginRequest("wrong"));
    expect(res.status).toBe(401);
  });

  it("rejects an empty password without calling the backend", async () => {
    const backend = mockBackend(200);
    vi.stubGlobal("fetch", backend);
    const { POST } = await import("@/app/api/auth/route");

    expect((await POST(loginRequest(""))).status).toBe(401);
    expect(backend).not.toHaveBeenCalled();
  });

  it("gives the same message whatever the reason for failure", async () => {
    vi.stubGlobal("fetch", mockBackend(403));
    const { POST } = await import("@/app/api/auth/route");

    const body = await (await POST(loginRequest("wrong"))).json();
    expect(body.error).toBe("Invalid password");
  });

  it("never echoes the submitted password", async () => {
    vi.stubGlobal("fetch", mockBackend(401));
    const { POST } = await import("@/app/api/auth/route");

    const text = await (await POST(loginRequest("hunter2"))).text();
    expect(text).not.toContain("hunter2");
  });
});

describe("login rate limiting", () => {
  beforeEach(() => {
    vi.resetModules();
    _resetRateLimits();
    process.env.SESSION_SECRET = "a-test-secret-long-enough-to-sign-tokens-with";
    process.env.NEXT_PUBLIC_API_URL = "http://backend.test";
  });

  afterEach(() => vi.unstubAllGlobals());

  it("blocks a brute-force run and returns 429", async () => {
    vi.stubGlobal("fetch", mockBackend(401));
    const { POST } = await import("@/app/api/auth/route");

    const codes: number[] = [];
    for (let i = 0; i < 14; i++) {
      codes.push((await POST(loginRequest(`guess-${i}`))).status);
    }

    expect(codes).toContain(401);
    expect(codes).toContain(429);
    expect(codes.at(-1)).toBe(429);
  });

  it("tells the caller when to come back", async () => {
    vi.stubGlobal("fetch", mockBackend(401));
    const { POST } = await import("@/app/api/auth/route");

    let res!: Response;
    for (let i = 0; i < 14; i++) res = await POST(loginRequest(`g${i}`));

    expect(res.status).toBe(429);
    expect(Number(res.headers.get("Retry-After"))).toBeGreaterThan(0);
  });

  it("does not lock out a person who mistypes twice", async () => {
    vi.stubGlobal("fetch", mockBackend(401));
    const { POST } = await import("@/app/api/auth/route");
    await POST(loginRequest("typo1"));
    await POST(loginRequest("typo2"));

    vi.stubGlobal("fetch", mockBackend(200));
    expect((await POST(loginRequest("correct"))).status).toBe(200);
  });

  it("limits per client, not globally", async () => {
    vi.stubGlobal("fetch", mockBackend(401));
    const { POST } = await import("@/app/api/auth/route");

    for (let i = 0; i < 14; i++) await POST(loginRequest(`g${i}`, "198.51.100.1"));
    // A second operator on a different address must not be locked out by the first.
    expect((await POST(loginRequest("x", "198.51.100.2"))).status).toBe(401);
  });

  it("recovers once the window passes", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal("fetch", mockBackend(401));
      const { POST } = await import("@/app/api/auth/route");

      let res!: Response;
      for (let i = 0; i < 14; i++) res = await POST(loginRequest(`g${i}`));
      expect(res.status).toBe(429);

      vi.advanceTimersByTime(61_000);
      vi.stubGlobal("fetch", mockBackend(200));
      expect((await POST(loginRequest("correct"))).status).toBe(200);
    } finally {
      vi.useRealTimers();
    }
  });

  it("passes a backend 429 through rather than masking it as a bad password", async () => {
    vi.stubGlobal("fetch", mockBackend(429, { "retry-after": "42" }));
    const { POST } = await import("@/app/api/auth/route");

    const res = await POST(loginRequest("correct"));
    expect(res.status).toBe(429);
    expect(res.headers.get("Retry-After")).toBe("42");
  });

  it("refuses to sign in when the backend is unreachable", async () => {
    // Falling back to a local password check would put the only copy of the secret in
    // whichever process happened to be up.
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    const { POST } = await import("@/app/api/auth/route");

    expect((await POST(loginRequest("correct"))).status).toBe(503);
  });
});

describe("logout", () => {
  beforeEach(() => {
    vi.resetModules();
    _resetRateLimits();
    process.env.SESSION_SECRET = "a-test-secret-long-enough-to-sign-tokens-with";
  });

  it("clears the session cookie", async () => {
    const { POST } = await import("@/app/api/auth/route");
    const res = await POST(
      new Request(POST_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "logout" }),
      }),
    );

    expect(res.status).toBe(200);
    expect(res.headers.get("set-cookie") ?? "").toContain("vasooli_dash=");
  });
});
