import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { liveGet, loginLive } from "@/lib/live-api";

/**
 * The access cookie lasts fifteen minutes; the refresh cookie lasts thirty days.
 * Nothing ever spent the refresh cookie, so a merchant was signed out a quarter of
 * an hour in — the workspace still looked signed in and every action answered "Live
 * authentication required". It is what made saving Razorpay keys fail: filling that
 * form takes longer than the token lives.
 */
function jsonResponse(status: number, body: unknown = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("expired live session", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("refreshes once and retries, instead of failing the merchant's action", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: "Live authentication required" }))
      .mockResolvedValueOnce(jsonResponse(200, { status: "rotated" }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));

    await expect(liveGet("/api/live/billing/subscription", "m-1")).resolves.toEqual({ ok: true });

    const paths = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(paths[1]).toBe("/api/live/auth/refresh");
    expect(paths[2]).toBe("/api/live/billing/subscription");
  });

  it("gives up when the refresh cookie has expired too", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: "Live authentication required" }))
      .mockResolvedValueOnce(jsonResponse(401, { detail: "Refresh token required" }));

    await expect(liveGet("/api/live/billing/subscription", "m-1")).rejects.toThrow(
      /live authentication required/i,
    );
    // No pointless retry of the original call once refresh has failed.
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not try to refresh a rejected sign-in", async () => {
    // A 401 here means the password is wrong, not that a session lapsed. Refreshing
    // would recurse and would tell the user the wrong thing.
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "Incorrect email or password" }));

    await expect(loginLive("a@b.example", "wrong")).rejects.toThrow(/incorrect email or password/i);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rotates once when several polls expire together", async () => {
    // Rotation invalidates the previous refresh token, so a stampede of concurrent
    // 401s each rotating would sign the merchant out rather than keep them in.
    fetchMock.mockImplementation((input: string) =>
      Promise.resolve(
        String(input).endsWith("/refresh")
          ? jsonResponse(200, { status: "rotated" })
          : fetchMock.mock.calls.filter((c) => !String(c[0]).endsWith("/refresh")).length <= 3
            ? jsonResponse(401, { detail: "Live authentication required" })
            : jsonResponse(200, { ok: true }),
      ),
    );

    await Promise.all([
      liveGet("/api/live/billing/subscription", "m-1"),
      liveGet("/api/live/dashboard/overview", "m-1"),
      liveGet("/api/live/dashboard/queue", "m-1"),
    ]);

    const refreshes = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/refresh"));
    expect(refreshes).toHaveLength(1);
  });
});
