import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import StartPage from "@/app/live/start/page";
import { liveGet, livePost, reauthLive } from "@/lib/live-api";

vi.mock("@/lib/live-api", () => ({
  liveGet: vi.fn(),
  livePost: vi.fn(),
  reauthLive: vi.fn(),
}));

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

/** Confirm the plan, then fill in the password form the button now opens. */
async function confirmWith(buttonPattern: RegExp) {
  fireEvent.click(await screen.findByRole("button", { name: buttonPattern }));
  const field = await screen.findByLabelText(/your password/i);
  fireEvent.change(field, { target: { value: "hunter2hunter2" } });
  fireEvent.click(screen.getByRole("button", { name: /continue to payment/i }));
}

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
    // The password is collected by a real form now, not a browser dialog.
    vi.spyOn(window, "open").mockReturnValue({ closed: false, close: vi.fn() } as never);
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
    await confirmWith(/Start 7-day trial — pay ₹2 now/);
    await waitFor(() => expect(livePost).toHaveBeenCalled());
    expect(vi.mocked(livePost).mock.calls[0][2]).toEqual({ plan_slug: "growth", start_trial: true });
  });

  it("charges the full plan when the merchant starts immediately", async () => {
    mockLoad(NEW_MERCHANT);
    render(<StartPage />);
    await confirmWith(/Start now — pay ₹5,999/);
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

  it("opens Razorpay in its own tab rather than navigating away", async () => {
    mockLoad(NEW_MERCHANT);
    const open = vi.spyOn(window, "open").mockReturnValue({ closed: false, close: vi.fn() } as never);
    vi.mocked(livePost).mockResolvedValue({ checkout_url: "https://rzp.io/x" } as never);

    render(<StartPage />);
    await confirmWith(/Start 7-day trial/);

    // A new tab, not `location.assign`. Navigating away meant an abandoned payment
    // dropped the merchant on Razorpay's page with no route back to their plan.
    await waitFor(() => expect(open).toHaveBeenCalledWith("https://rzp.io/x", "_blank", expect.anything()));
    // This page stays put and waits, so a cancelled payment returns to the choice.
    expect(await screen.findByText(/waiting for your payment/i)).toBeInTheDocument();
  });

  it("tells the merchant when the browser blocks the payment window", async () => {
    mockLoad(NEW_MERCHANT);
    vi.spyOn(window, "open").mockReturnValue(null);
    vi.mocked(livePost).mockResolvedValue({ checkout_url: "https://rzp.io/x" } as never);

    render(<StartPage />);
    await confirmWith(/Start 7-day trial/);
    // Silence here looked like a broken button.
    expect(await screen.findByRole("alert")).toHaveTextContent(/pop-ups/i);
  });
});
