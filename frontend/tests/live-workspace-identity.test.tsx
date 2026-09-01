import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/AppShell";

vi.mock("next/navigation", () => ({
  usePathname: () => "/live",
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
}));

describe("live workspace identity", () => {
  beforeEach(() => {
    window.localStorage.setItem("vasooli_live_merchant", "merchant-1");
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
    });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/api/live/workspace/profile")) {
        return new Response(JSON.stringify({
          business_name: "Maurya Enterprises",
          subscription: { label: "Starter", slug: "starter", status: "active", trial_ends_at: null },
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows the tenant business name and subscription", async () => {
    render(<AppShell guidedSignedIn={false}><div>Dashboard</div></AppShell>);
    await waitFor(() => expect(screen.getByText("Maurya Enterprises")).toBeInTheDocument());
    expect(screen.getByText("Starter")).toBeInTheDocument();
    expect(screen.getByText("Active subscription · Live mode")).toBeInTheDocument();
  });
});
