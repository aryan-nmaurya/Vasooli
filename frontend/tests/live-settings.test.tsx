import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LiveSettingsGeneralPage from "@/app/live/settings/page";
import LiveSettingsLayout from "@/app/live/settings/layout";
import { LiveSettingsNav, isActiveSettingsSection } from "@/components/LiveSettingsNav";
import { liveGet } from "@/lib/live-api";

const pathname = vi.fn(() => "/live/settings");

vi.mock("next/navigation", () => ({ usePathname: () => pathname() }));
vi.mock("@/lib/live-api", () => ({ liveGet: vi.fn(), livePost: vi.fn() }));

describe("live settings navigation", () => {
  beforeEach(() => {
    pathname.mockReturnValue("/live/settings");
    vi.mocked(liveGet).mockResolvedValue([] as never);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("links every section under /live/settings", () => {
    render(<LiveSettingsNav />);
    const nav = screen.getByRole("navigation", { name: "Settings sections" });

    expect(nav).toBeInTheDocument();
    for (const [name, href] of [
      ["General", "/live/settings"],
      ["Integrations", "/live/settings/integrations"],
      ["Recovery policy", "/live/settings/policy"],
      ["Billing", "/live/settings/billing"],
      ["Team access", "/live/settings/team"],
    ] as const) {
      expect(screen.getByRole("link", { name })).toHaveAttribute("href", href);
    }
  });

  it("marks only the current section as active", () => {
    pathname.mockReturnValue("/live/settings/billing");
    render(<LiveSettingsNav />);

    expect(screen.getByRole("link", { name: "Billing" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "General" })).not.toHaveAttribute("aria-current");
  });

  it("does not treat General as active on nested sections", () => {
    // A prefix match would light up General everywhere, which is the bug this guards.
    expect(isActiveSettingsSection("/live/settings/team", "/live/settings")).toBe(false);
    expect(isActiveSettingsSection("/live/settings", "/live/settings")).toBe(true);
    expect(isActiveSettingsSection("/live/settings/team", "/live/settings/team")).toBe(true);
  });

  it("renders the shared heading and rail around each section", () => {
    render(<LiveSettingsLayout>{<p>Section body</p>}</LiveSettingsLayout>);

    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Settings sections" })).toBeInTheDocument();
    expect(screen.getByText("Section body")).toBeInTheDocument();
  });
});

describe("live settings general section", () => {
  beforeEach(() => {
    window.localStorage.setItem("vasooli_live_merchant", "merchant-1");
    vi.mocked(liveGet).mockImplementation(async (path) => {
      if (path.includes("operations/readiness")) {
        return {
          status: "ready",
          summary: "All scheduled work is current.",
          jobs: {
            recovery_cycle: { last_started_at: new Date().toISOString(), status: "completed", stale: false },
          },
        } as never;
      }
      return [] as never;
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("shows automation and sender identity without a system-health page", async () => {
    render(<LiveSettingsGeneralPage />);

    expect(
      await screen.findByRole("heading", { name: "Automation is running on schedule." }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sender identity" })).toBeInTheDocument();
    expect(screen.queryByText(/System health/i)).not.toBeInTheDocument();
  });
});
