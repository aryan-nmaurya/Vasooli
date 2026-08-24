/**
 * The dispute-resolve path through the action proxy. Customer Conversation Safety.
 *
 * The proxy is what attaches the admin key, so an allowlist mistake here turns the
 * one write path out of a dispute into a credentialed open endpoint. These tests pin
 * the two things that matter: the route is reachable when it should be, and only
 * that exact shape is.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const CASE_ID = "3f1c9e58-4a2b-4d7e-9c11-8ab6f0e2d4a7";

vi.mock("@/lib/session", () => ({
  currentSession: vi.fn(async () => ({ sub: "ops@example.com" })),
}));

async function post(path: string, body: unknown = {}) {
  const { POST } = await import("@/app/api/action/route");
  return POST(
    new Request("http://localhost:3000/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, body }),
    }),
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ resumed: true }), { status: 200 }),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("resolving a dispute through the proxy", () => {
  it("allows the resolve path", async () => {
    const res = await post(`/api/dashboard/disputes/${CASE_ID}/resolve`, {
      note: "Delivery note checked.",
      resume_recovery: true,
    });
    expect(res.status).toBe(200);
  });

  it("attaches the admin key server-side", async () => {
    await post(`/api/dashboard/disputes/${CASE_ID}/resolve`);
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const init = call[1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-Admin-Key"]).toBeTruthy();
  });

  it("forwards the merchant's decision unchanged", async () => {
    await post(`/api/dashboard/disputes/${CASE_ID}/resolve`, {
      note: "Credit note issued.",
      resume_recovery: false,
    });
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const body = JSON.parse((call[1] as RequestInit).body as string);
    expect(body).toEqual({ note: "Credit note issued.", resume_recovery: false });
  });

  it("rejects a case id that is not a uuid", async () => {
    const res = await post("/api/dashboard/disputes/../../admin/run-cycle/resolve");
    expect(res.status).toBe(400);
  });

  it("rejects any other verb on a dispute case", async () => {
    /** There is no delete-a-dispute endpoint, and the proxy must not invent one. */
    const res = await post(`/api/dashboard/disputes/${CASE_ID}/delete`);
    expect(res.status).toBe(400);
  });

  it("rejects listing disputes through the write proxy", async () => {
    const res = await post("/api/dashboard/disputes");
    expect(res.status).toBe(400);
  });
});

describe("without a session", () => {
  it("refuses before the admin key is attached to anything", async () => {
    const session = await import("@/lib/session");
    vi.mocked(session.currentSession).mockResolvedValueOnce(null);

    const res = await post(`/api/dashboard/disputes/${CASE_ID}/resolve`);
    expect(res.status).toBe(401);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});
