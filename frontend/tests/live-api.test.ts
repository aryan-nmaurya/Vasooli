import { afterEach, describe, expect, it, vi } from "vitest";

import { isPaymentRequired, LiveApiError, registerLive } from "@/lib/live-api";

function respondWith(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    }),
  );
}

const payload = {
  email: "owner@acme.test",
  password: "CorrectHorse9Battery",
  legal_business_name: "Acme",
  accept_terms: true,
  accept_privacy: true,
};

describe("live api errors", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders a validation error as a sentence, not [object Object]", async () => {
    // FastAPI answers 422 with `detail` as an ARRAY of objects. It used to be passed
    // straight into the Error message, so the signup form told the merchant
    // "[object Object]" when their email address was rejected.
    respondWith(422, {
      detail: [
        {
          type: "value_error",
          loc: ["body", "email"],
          msg: "value is not a valid email address: The part after the @-sign is reserved.",
        },
      ],
    });

    await expect(registerLive(payload)).rejects.toThrow(/not a valid email address/);
    await expect(registerLive(payload)).rejects.not.toThrow(/object Object/);
  });

  it("joins more than one validation failure", async () => {
    respondWith(422, {
      detail: [{ msg: "Password is too short" }, { msg: "Business name is required" }],
    });
    await expect(registerLive(payload)).rejects.toThrow(
      "Password is too short. Business name is required",
    );
  });

  it("passes a plain string detail through unchanged", async () => {
    respondWith(409, { detail: "That workspace already exists" });
    await expect(registerLive(payload)).rejects.toThrow("That workspace already exists");
  });

  it("falls back to the status when the body carries nothing usable", async () => {
    respondWith(500, {});
    await expect(registerLive(payload)).rejects.toThrow("Request failed (500)");
  });

  it("still identifies a payment-required response for the gate", async () => {
    respondWith(402, { detail: "Your subscription is not active." });
    const caught = await registerLive(payload).catch((cause) => cause);
    expect(caught).toBeInstanceOf(LiveApiError);
    expect(isPaymentRequired(caught)).toBe(true);
  });
});
