import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

async function callLiveProxy(headers: Record<string, string> = {}) {
  const { POST } = await import("@/app/api/live/[...path]/route");
  return POST(
    new Request("http://localhost:3000/api/live/billing/checkout", {
      method: "POST",
      headers: { "content-type": "application/json", ...headers },
      body: JSON.stringify({ plan_slug: "starter" }),
    }),
    { params: Promise.resolve({ path: ["billing", "checkout"] }) },
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("live API passthrough", () => {
  it("forwards the single-use re-authentication proof", async () => {
    await callLiveProxy({
      cookie: "vasooli_live_access=signed",
      "x-merchant-id": "ca978112-ca1b-4dca-bac2-31b39a23dc4d",
      "x-reauth-token": "fresh-proof",
    });

    const init = vi.mocked(globalThis.fetch).mock.calls[0][1] as RequestInit;
    const headers = init.headers as Headers;
    expect(headers.get("x-reauth-token")).toBe("fresh-proof");
    expect(headers.get("x-merchant-id")).toBe("ca978112-ca1b-4dca-bac2-31b39a23dc4d");
  });

  it("does not disclose the private upstream address during an outage", async () => {
    vi.mocked(globalThis.fetch).mockRejectedValueOnce(new Error("connect ECONNREFUSED"));
    const response = await callLiveProxy();

    expect(response.status).toBe(502);
    const body = await response.text();
    expect(body).toContain("temporarily unavailable");
    expect(body).not.toContain("localhost:8000");
    expect(body).not.toContain("ECONNREFUSED");
  });
});
