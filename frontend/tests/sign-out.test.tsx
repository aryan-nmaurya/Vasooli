import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SignOutButton } from "@/components/SignOutButton";

const replace = vi.fn();
const refresh = vi.fn();
let pathname = "/";

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ replace, refresh }),
}));

describe("SignOutButton", () => {
  beforeEach(() => {
    pathname = "/";
    replace.mockReset();
    refresh.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("clears the session and returns to login", async () => {
    const request = vi.fn(async () => new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", request);
    render(<SignOutButton signedIn />);

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(request).toHaveBeenCalledWith(
      "/api/auth",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ action: "logout" }),
      }),
    );
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("does not show a sign-out action on the login page", () => {
    pathname = "/login";
    render(<SignOutButton signedIn />);
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("stays in place and exposes a retry state when logout fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 503 })));
    render(<SignOutButton signedIn />);

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    expect(
      await screen.findByRole("button", { name: "Sign out failed — try again" }),
    ).toBeEnabled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("does not show a sign-out action to an anonymous visitor", () => {
    render(<SignOutButton signedIn={false} />);
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });
});
