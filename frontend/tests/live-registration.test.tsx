import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LiveRegistrationForm } from "@/components/LiveRegistrationForm";
import { registerLive } from "@/lib/live-api";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh: vi.fn() }) }));
vi.mock("@/lib/live-api", () => ({
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
    expect(screen.getByLabelText(/six-digit code/i)).toHaveValue("");
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
    expect(screen.getByText(/fresh verification code was sent/i)).toBeInTheDocument();
  });
});
