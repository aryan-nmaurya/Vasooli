import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DemoExitGuard } from "@/components/DemoExitGuard";
import { LiveExitGuard } from "@/components/LiveExitGuard";
import { logoutLive } from "@/lib/live-api";

const replace = vi.fn();
const refresh = vi.fn();

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, refresh }) }));
vi.mock("@/lib/live-api", () => ({ logoutLive: vi.fn() }));

/** Back lands on the sentinel entry the guard installed on mount. */
function pressBack() {
  fireEvent.popState(window, { state: { vasooliExitSentinel: true } });
}

const dialog = { name: "Confirm sign out" } as const;

describe("browser Back exit guard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.setItem("vasooli_live_merchant", "merchant-1");
  });

  afterEach(cleanup);

  it("shows the in-app dialog rather than a native confirm", () => {
    const nativeConfirm = vi.spyOn(window, "confirm");
    render(<LiveExitGuard />);

    expect(screen.queryByRole("dialog", dialog)).not.toBeInTheDocument();
    pressBack();

    expect(screen.getByRole("dialog", dialog)).toBeInTheDocument();
    expect(screen.getByText("Sign out of Vasooli?")).toBeInTheDocument();
    expect(nativeConfirm).not.toHaveBeenCalled();
  });

  it("signs the live user out and lands on the landing page", async () => {
    vi.mocked(logoutLive).mockResolvedValue({ status: "ok" });
    render(<LiveExitGuard />);
    pressBack();

    fireEvent.click(screen.getByRole("button", { name: "Yes, sign out" }));

    await waitFor(() => expect(logoutLive).toHaveBeenCalledOnce());
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(refresh).toHaveBeenCalledOnce();
    expect(window.localStorage.getItem("vasooli_live_merchant")).toBeNull();
  });

  it("stays in the workspace when the user declines", () => {
    render(<LiveExitGuard />);
    pressBack();

    fireEvent.click(screen.getByRole("button", { name: "No, stay signed in" }));

    expect(screen.queryByRole("dialog", dialog)).not.toBeInTheDocument();
    expect(logoutLive).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
    expect(window.localStorage.getItem("vasooli_live_merchant")).toBe("merchant-1");
  });

  it("keeps guarding after a decline", () => {
    render(<LiveExitGuard />);
    pressBack();
    fireEvent.click(screen.getByRole("button", { name: "No, stay signed in" }));

    pressBack();

    expect(screen.getByRole("dialog", dialog)).toBeInTheDocument();
  });

  it("guards the demo dashboard the same way", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
    render(<DemoExitGuard />);
    pressBack();

    expect(screen.getByRole("dialog", dialog)).toBeInTheDocument();
    expect(screen.getByText("Your dashboard session will end on this device.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Yes, sign out" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/auth",
        expect.objectContaining({ body: JSON.stringify({ action: "logout" }) }),
      ),
    );
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    vi.unstubAllGlobals();
  });
});
