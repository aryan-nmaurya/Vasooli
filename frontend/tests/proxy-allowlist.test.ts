import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/session", () => ({ currentSessionToken: vi.fn().mockResolvedValue("tok") }));

import { GET } from "@/app/api/proxy/[...path]/route";

/**
 * The read proxy is an allowlist, so a path the client calls but the list omits fails
 * as `400 path not allowed` — which every caller reads as "the feature is off".
 *
 * That is exactly how the Workspace settings entry disappeared from the LOCAL demo:
 * the shell asked the backend whether demo controls exist, the proxy refused the
 * path, and the shell concluded they did not. Mocking the api helper in a component
 * test cannot catch it, because the helper is where the failure is.
 */
async function proxy(path: string[]) {
  const upstream = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response('{"ok":true}', { status: 200 }));
  const res = await GET(new Request(`http://localhost:3000/api/proxy/${path.join("/")}`), {
    params: Promise.resolve({ path }),
  });
  upstream.mockRestore();
  return res;
}

describe("read proxy allowlist", () => {
  it("passes every path the browser actually asks for", async () => {
    // Each of these is called from a client component through `lib/api`.
    for (const path of [
      ["dashboard", "overview"],
      ["dashboard", "queue"],
      ["demo", "clock"],
    ]) {
      const res = await proxy(path);
      expect(res.status, `${path.join("/")} was refused by the allowlist`).toBe(200);
    }
  });

  it("still refuses anything not on the list", async () => {
    expect((await proxy(["admin", "secrets"])).status).toBe(400);
    // Not a read: state changes go through /api/action, which has its own list.
    expect((await proxy(["demo", "advance"])).status).toBe(400);
  });
});
