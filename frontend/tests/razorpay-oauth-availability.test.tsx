import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import IntegrationsPage from "@/app/live/settings/integrations/page";
import { liveGet } from "@/lib/live-api";

vi.mock("@/lib/live-api", () => ({
  liveGet: vi.fn(),
  livePut: vi.fn(),
  livePost: vi.fn(),
  reauthLive: vi.fn(),
  LiveApiError: class extends Error {},
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

/**
 * "Connect securely" starts Razorpay Partner OAuth, which needs client credentials
 * Razorpay issues to an approved partner. Without them `oauth/start` answers 503 —
 * so the button took the merchant's password and gave back an error with nothing to
 * act on. A route that cannot work is not offered.
 */
function mockBackend({ oauth }: { oauth: boolean }) {
  vi.mocked(liveGet).mockImplementation((path: string) => {
    if (path.includes("/capabilities")) return Promise.resolve({ oauth_available: oauth } as never);
    if (path.includes("/payment-connections")) return Promise.resolve(null as never);
    return Promise.resolve([] as never);
  });
}

describe("Razorpay connect options", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("hides the OAuth route on a deployment without partner credentials", async () => {
    window.localStorage.setItem("vasooli_live_merchant", "m-1");
    mockBackend({ oauth: false });

    render(<IntegrationsPage />);

    // The keys form is the route that does work, so it stays.
    await waitFor(() => expect(screen.getByText(/razorpay collections/i)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /connect securely/i })).toBeNull();
  });

  it("offers it once the deployment is configured for it", async () => {
    window.localStorage.setItem("vasooli_live_merchant", "m-1");
    mockBackend({ oauth: true });

    render(<IntegrationsPage />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /connect securely/i })).toBeInTheDocument(),
    );
  });
});
