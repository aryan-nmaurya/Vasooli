import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import StartPage from "@/app/live/start/page";
import { liveGet, livePost, reauthLive } from "@/lib/live-api";

vi.mock("@/lib/live-api", () => ({
  liveGet: vi.fn(),
  livePost: vi.fn(),
  reauthLive: vi.fn(),
}));

const PLANS = [
  { slug: "starter", name: "Starter", amount_paise: 199900, included_active_invoices: 100, included_seats: 1, features: [] },
  { slug: "growth", name: "Growth", amount_paise: 599900, included_active_invoices: 500, included_seats: 5, features: [] },
];

/** A merchant who has never subscribed: the trial and its ₹2 verification are on offer. */
const NEW_MERCHANT = { mandate_verification_paise: 200, trial_days: 7 };
/** A returning merchant: the trial is spent, so only the paid path exists. */
const RETURNING = { mandate_verification_paise: null };

function mockLoad(state: unknown) {
  vi.mocked(liveGet).mockImplementation((path: string) =>
    Promise.resolve((path.includes("/plans") ? PLANS : state) as never),
  );
}

describe("activation page", () => {
  beforeEach(() => {
    window.localStorage.setItem("vasooli_live_merchant", "m-1");
    window.localStorage.setItem("vasooli_pending_plan", "growth");
    vi.mocked(reauthLive).mockResolvedValue({ status: "ok", reauth_token: "t" } as never);
    vi.mocked(livePost).mockResolvedValue({ checkout_url: null } as never);
    vi.spyOn(window, "prompt").mockReturnValue("hunter2hunter2");
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("opens on the plan chosen during signup", async () => {
    mockLoad(NEW_MERCHANT);
    render(<StartPage />);
    // ₹5,999 is Growth, carried here in storage rather than asked for a second time.
    expect(await screen.findByRole("button", { name: /Start now — pay ₹5,999/ })).toBeEnabled();
  });

  it("offers the trial at ₹2, not the plan amount", async () => {
    mockLoad(NEW_MERCHANT);
    render(<StartPage />);
    const trial = await screen.findByRole("button", { name: /Start 7-day trial — pay ₹2 now/ });
    fireEvent.click(trial);
    await waitFor(() => expect(livePost).toHaveBeenCalled());
    expect(vi.mocked(livePost).mock.calls[0][2]).toEqual({ plan_slug: "growth", start_trial: true });
  });

  it("charges the full plan when the merchant starts immediately", async () => {
    mockLoad(NEW_MERCHANT);
    render(<StartPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Start now — pay ₹5,999/ }));
    await waitFor(() => expect(livePost).toHaveBeenCalled());
    expect(vi.mocked(livePost).mock.calls[0][2]).toEqual({ plan_slug: "growth", start_trial: false });
  });

  it("hides the trial from a merchant who has already had one", async () => {
    mockLoad(RETURNING);
    render(<StartPage />);
    await screen.findByRole("button", { name: /Start now — pay ₹5,999/ });
    // The server would refuse a second trial anyway; offering it would be a promise
    // the checkout then breaks by charging the full amount.
    expect(screen.queryByRole("button", { name: /Start 7-day trial/ })).toBeNull();
  });

  it("sends the merchant to Razorpay when a checkout url comes back", async () => {
    mockLoad(NEW_MERCHANT);
    const assign = vi.fn();
    Object.defineProperty(window, "location", { value: { ...window.location, assign }, writable: true });
    vi.mocked(livePost).mockResolvedValue({ checkout_url: "https://rzp.io/x" } as never);

    render(<StartPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Start 7-day trial/ }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://rzp.io/x"));
    // The pending choice is spent once it has been acted on, or a later visit would
    // silently reopen on a plan the merchant already paid for.
    expect(window.localStorage.getItem("vasooli_pending_plan")).toBeNull();
  });
});
