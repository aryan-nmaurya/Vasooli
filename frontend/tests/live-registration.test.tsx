import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LiveRegistrationForm } from "@/components/LiveRegistrationForm";
import { loginLive, registerLive, verifyLiveCode } from "@/lib/live-api";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh: vi.fn() }) }));
vi.mock("@/lib/live-api", () => ({
  API_BASE: "",
  registerLive: vi.fn(),
  verifyLiveCode: vi.fn(),
  loginLive: vi.fn(),
}));

describe("live registration", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("requires both legal acknowledgements and advances to email OTP verification", async () => {
    vi.mocked(registerLive).mockResolvedValue({
      status: "verification_required",
      merchant_id: "merchant-1",
      verification_token: "123456",
    });
    render(<LiveRegistrationForm />);

    const submit = screen.getByRole("button", { name: /create workspace and verify email/i });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/business name/i), { target: { value: "Acme Finance" } });
    fireEvent.change(screen.getByLabelText(/work email/i), { target: { value: "owner@acme.test" } });
    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: "CorrectHorse9Battery" } });
    fireEvent.click(screen.getByLabelText(/i agree to the terms/i));
    expect(submit).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/i have read the privacy/i));
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => expect(registerLive).toHaveBeenCalled());
    expect(await screen.findByRole("heading", { name: /verify your work email/i })).toBeInTheDocument();
    // In dry-run the API returns the code and the form pre-fills it, so a local
    // reviewer is not asked to read an email that was never sent. Production returns
    // no token and the field stays empty.
    expect(screen.getByLabelText(/six-digit code/i)).toHaveValue("123456");
    expect(screen.getByText(/no email was sent/i)).toBeInTheDocument();
  });

  it("resends through the recoverable registration flow after a short cooldown", async () => {
    vi.useFakeTimers();
    vi.mocked(registerLive).mockResolvedValue({
      status: "verification_required",
      merchant_id: "merchant-1",
      verification_token: null,
    });
    render(<LiveRegistrationForm />);

    fireEvent.change(screen.getByLabelText(/business name/i), { target: { value: "Acme Finance" } });
    fireEvent.change(screen.getByLabelText(/work email/i), { target: { value: "OWNER@ACME.TEST" } });
    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: "UpdatedHorse9Battery" } });
    fireEvent.click(screen.getByLabelText(/i agree to the terms/i));
    fireEvent.click(screen.getByLabelText(/i have read the privacy/i));
    fireEvent.click(screen.getByRole("button", { name: /create workspace and verify email/i }));

    await act(async () => Promise.resolve());
    expect(screen.getByRole("button", { name: /resend code in 30s/i })).toBeDisabled();
    for (let second = 0; second < 30; second += 1) {
      await act(async () => vi.advanceTimersByTimeAsync(1_000));
    }
    fireEvent.click(screen.getByRole("button", { name: /^resend code$/i }));
    await act(async () => Promise.resolve());

    expect(registerLive).toHaveBeenCalledTimes(2);
    expect(registerLive).toHaveBeenLastCalledWith(expect.objectContaining({
      email: "owner@acme.test",
      password: "UpdatedHorse9Battery",
    }));
    // No token in this mock, so the copy must not promise mail was sent: registering
    // an address that already exists deliberately sends nothing, and claiming
    // otherwise both misleads the sender and leaks who holds an account.
    expect(screen.getByText(/a fresh code is on its way/i)).toBeInTheDocument();
    expect(screen.getByText(/earlier codes no longer work/i)).toBeInTheDocument();
  });

  it("asks a verified merchant to choose a plan before the workspace opens", async () => {
    vi.mocked(registerLive).mockResolvedValue({
      status: "verification_required",
      merchant_id: "merchant-1",
      verification_token: "123456",
    });
    vi.mocked(verifyLiveCode).mockResolvedValue({ status: "verified" } as never);
    vi.mocked(loginLive).mockResolvedValue({ merchants: ["merchant-1"] } as never);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => [
          { slug: "starter", name: "Starter", amount_paise: 199900, included_active_invoices: 100, included_seats: 1 },
          { slug: "growth", name: "Growth", amount_paise: 599900, included_active_invoices: 500, included_seats: 5 },
        ],
      }),
    );

    render(<LiveRegistrationForm />);
    fireEvent.change(screen.getByLabelText(/business name/i), { target: { value: "Acme Finance" } });
    fireEvent.change(screen.getByLabelText(/work email/i), { target: { value: "owner@acme.test" } });
    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: "CorrectHorse9Battery" } });
    fireEvent.click(screen.getByLabelText(/i agree to the terms/i));
    fireEvent.click(screen.getByLabelText(/i have read the privacy/i));
    fireEvent.click(screen.getByRole("button", { name: /create workspace and verify email/i }));
    await waitFor(() => expect(registerLive).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: /verify email and continue/i }));

    // A brand-new workspace has no subscription, so signing in must not drop the
    // merchant on a dashboard that would bounce them back on the first write.
    expect(await screen.findByRole("heading", { name: /choose your plan/i })).toBeInTheDocument();
    // A real disabled button, so it looks inert and cannot be activated by keyboard
    // either — as a styled link it stayed full-strength and `pointerEvents: none`
    // stopped only the mouse.
    const cta = screen.getByRole("button", { name: /continue to payment/i });
    expect(cta).toBeDisabled();

    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      value: { ...window.location, assign },
      writable: true,
    });

    fireEvent.click(await screen.findByRole("button", { name: /Growth/ }));
    expect(cta).toBeEnabled();
    // Checkout needs an authenticated session, so the choice is carried in storage
    // and the billing page pre-selects it rather than asking twice.
    fireEvent.click(cta);
    expect(window.localStorage.getItem("vasooli_pending_plan")).toBe("growth");
    expect(assign).toHaveBeenCalledWith("/live/settings/billing?reason=new_signup");
  });

  it("does not promise a free trial while telling the merchant no card is needed", () => {
    render(<LiveRegistrationForm />);
    // The mandate makes a payment instrument mandatory to start the trial, so the
    // old "No card required" line was a promise the flow no longer keeps.
    expect(screen.queryByText(/no card required/i)).not.toBeInTheDocument();
  });
});
