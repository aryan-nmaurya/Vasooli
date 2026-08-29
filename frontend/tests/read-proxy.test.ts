/**
 * The authenticated read proxy.
 *
 * Checks that every allowed read path passes through with the user session attached,
 * and that any unrecognized or unauthenticated request is rejected.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/session", () => ({
  currentSessionToken: vi.fn(async () => "signed-user-session"),
}));

async function getProxy(pathSegments: string[], query = "") {
  const { GET } = await import("@/app/api/proxy/[...path]/route");
  const url = `http://localhost:3000/api/proxy/${pathSegments.join("/")}${query ? `?${query}` : ""}`;
  return GET(
    new Request(url, { method: "GET" }),
    { params: Promise.resolve({ path: pathSegments }) },
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ status: "ok" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("GET /api/proxy/[...path]", () => {
  it.each([
    [["dashboard", "overview"]],
    [["dashboard", "queue"]],
    [["dashboard", "promises"]],
    [["dashboard", "disputes"]],
    [["dashboard", "audit"]],
    [["dashboard", "exceptions"]],
    [["dashboard", "invoices", "3f1c9e58-4a2b-4d7e-9c11-8ab6f0e2d4a7"]],
    [["invoices"]],
    [["invoices", "3f1c9e58-4a2b-4d7e-9c11-8ab6f0e2d4a7"]],
  ])("allows %j", async (segments) => {
    const res = await getProxy(segments);
    expect(res.status).toBe(200);
  });

  it("forwards the backend-issued user session server-side", async () => {
    await getProxy(["dashboard", "disputes"]);
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const init = call[1] as RequestInit;
    expect((init.headers as Record<string, string>).Cookie).toBe(
      "vasooli_session=signed-user-session",
    );
    expect((init.headers as Record<string, string>)["X-Admin-Key"]).toBeUndefined();
  });

  it("forwards query parameters properly", async () => {
    await getProxy(["dashboard", "queue"], "limit=50&reason=oversight");
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(call[0])).toContain("limit=50&reason=oversight");
  });

  it("rejects paths not in the allowlist", async () => {
    const res = await getProxy(["unknown", "path"]);
    expect(res.status).toBe(400);
  });

  it("rejects unauthorized access without a session", async () => {
    const session = await import("@/lib/session");
    vi.mocked(session.currentSessionToken).mockResolvedValueOnce(null);

    const res = await getProxy(["dashboard", "overview"]);
    expect(res.status).toBe(401);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});
