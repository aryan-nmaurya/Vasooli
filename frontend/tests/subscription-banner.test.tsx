import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SubscriptionBanner } from "@/components/SubscriptionBanner";
import { liveGet } from "@/lib/live-api";

vi.mock("@/lib/live-api", () => ({ liveGet: vi.fn() }));

const base = {
  status: "active",
  plan: { slug: "growth", name: "Growth", amount_paise: 599900, included_active_invoices: 500, included_seats: 5, features: [] },
  is_active: true,
  on_trial: false,
  days_remaining: 20,
  period_end: null,
  cancel_at_period_end: false,
  paused_reason: null,
  warning: null,
  provider_subscription_id: null,
};

function state(overrides: Record<string, unknown>) {
  vi.mocked(liveGet).mockResolvedValue({ ...base, ...overrides } as never);
}

describe("subscription banner", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.setItem("vasooli_live_merchant", "merchant-1");
  });
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
  });

  it("stays out of the way while the subscription is healthy", async () => {
    state({});
    render(<SubscriptionBanner />);
    await waitFor(() => expect(liveGet).toHaveBeenCalled());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("says automation paused, and that the data is still there", async () => {
    state({ is_active: false, status: "expired", paused_reason: "Your subscription has expired." });
    render(<SubscriptionBanner />);

    expect(await screen.findByText("Automation paused")).toBeInTheDocument();
    // The whole point of the read-only decision: never imply their records are gone.
    expect(screen.getByRole("status")).toHaveTextContent(/data remains available/i);
    expect(screen.getByRole("link", { name: "Resume automation" })).toHaveAttribute(
      "href",
      "/live/settings/billing",
    );
  });

  it("warns without alarming while a failed payment is still in grace", async () => {
    state({ is_active: true, status: "past_due", paused_reason: null, warning: "Your last payment failed." });
    render(<SubscriptionBanner />);

    expect(await screen.findByText("Payment needs attention")).toBeInTheDocument();
    expect(screen.getByRole("status")).not.toHaveTextContent(/paused/i);
  });

  it("nudges near the end of the trial but not at the start of it", async () => {
    state({ on_trial: true, status: "trialing", days_remaining: 2 });
    render(<SubscriptionBanner />);
    expect(await screen.findByText("Trial ends in 2 days")).toBeInTheDocument();

    cleanup();
    state({ on_trial: true, status: "trialing", days_remaining: 6 });
    render(<SubscriptionBanner />);
    await waitFor(() => expect(liveGet).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("fails open when billing cannot be read", async () => {
    vi.mocked(liveGet).mockRejectedValue(new Error("network"));
    render(<SubscriptionBanner />);
    await waitFor(() => expect(liveGet).toHaveBeenCalled());
    // A failed lookup must never be rendered as "you are unpaid".
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
