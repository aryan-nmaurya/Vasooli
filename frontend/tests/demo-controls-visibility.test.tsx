import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/AppShell";

vi.mock("@/lib/api", () => ({
  getDemoClock: vi.fn(),
  // The shell also renders the runtime banner; it is not what this file is about.
  getRuntimeSafety: vi.fn().mockRejectedValue(new Error("not under test")),
}));
vi.mock("@/lib/live-api", () => ({ liveGet: vi.fn().mockRejectedValue(new Error("no")) }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { getDemoClock } from "@/lib/api";

/**
 * The demo controls are the ONLY thing behind Workspace settings, and production
 * refuses to boot with them enabled. So on production that sidebar entry led to a
 * page whose entire content was "Demo controls are unavailable in this environment"
 * — a dead end a real merchant could click into from their own workspace.
 */
describe("Workspace settings entry", () => {
  beforeEach(() => {
    window.localStorage.clear();
    // jsdom has no matchMedia; the shell reads it to pick a theme.
    window.matchMedia = vi.fn().mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }) as never;
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("is shown where the demo controls actually exist", async () => {
    vi.mocked(getDemoClock).mockResolvedValue({ offset_days: 0 } as never);
    render(<AppShell guidedSignedIn>{null}</AppShell>);
    await waitFor(() =>
      expect(screen.getAllByText(/workspace settings/i).length).toBeGreaterThan(0),
    );
  });

  it("is hidden when the backend has them switched off", async () => {
    // What production returns: the demo router refuses, so the clock never resolves.
    vi.mocked(getDemoClock).mockRejectedValue(new Error("demo controls disabled"));
    render(<AppShell guidedSignedIn>{null}</AppShell>);
    await waitFor(() => expect(getDemoClock).toHaveBeenCalled());
    expect(screen.queryByText(/workspace settings/i)).toBeNull();
  });

  it("never flashes the entry before the answer arrives", () => {
    // Starts hidden rather than shown: a control that appears and then vanishes is
    // worse than one that appears a beat late.
    vi.mocked(getDemoClock).mockReturnValue(new Promise(() => {}) as never);
    render(<AppShell guidedSignedIn>{null}</AppShell>);
    expect(screen.queryByText(/workspace settings/i)).toBeNull();
  });
});
