import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LiveSignOutButton } from "@/components/LiveSignOutButton";
import { logoutLive } from "@/lib/live-api";

const replace = vi.fn();
const refresh = vi.fn();

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, refresh }) }));
vi.mock("@/lib/live-api", () => ({ logoutLive: vi.fn() }));

describe("LiveSignOutButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.setItem("vasooli_live_merchant", "merchant-1");
  });

  afterEach(cleanup);

  it("asks for confirmation before signing out", async () => {
    vi.mocked(logoutLive).mockResolvedValue({ status: "ok" });
    render(<LiveSignOutButton />);
    const signOutButton = await screen.findByRole("button", { name: "Sign out" });

    fireEvent.click(signOutButton);
    expect(logoutLive).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Confirm sign out" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Yes, sign out" }));
    await waitFor(() => expect(logoutLive).toHaveBeenCalledOnce());
    expect(replace).toHaveBeenCalledWith("/live/login");
    expect(refresh).toHaveBeenCalledOnce();
    expect(window.localStorage.getItem("vasooli_live_merchant")).toBeNull();
  });

  it("keeps the live session when sign out is cancelled", async () => {
    render(<LiveSignOutButton />);
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    fireEvent.click(screen.getByRole("button", { name: "No, stay signed in" }));

    expect(logoutLive).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog", { name: "Confirm sign out" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem("vasooli_live_merchant")).toBe("merchant-1");
  });
});
