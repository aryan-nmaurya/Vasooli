/**
 * The money-recording paths through the action proxy.
 *
 * The proxy forwards the backend-issued user session, so an allowlist mistake turns
 * a write path into a credentialed open endpoint. These paths change what the system
 * believes it has been paid, which makes them the most consequential entries on the
 * list — and the most important ones to keep narrow.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const INVOICE_ID = "3f1c9e58-4a2b-4d7e-9c11-8ab6f0e2d4a7";
const PAYMENT_ID = "9b2d1f04-77ac-4e31-b0d5-1c6e8f3a2b45";

vi.mock("@/lib/session", () => ({
  currentSessionToken: vi.fn(async () => "signed-user-session"),
}));

async function post(path: string, body: unknown = {}) {
  const { POST } = await import("@/app/api/action/route");
  return POST(
    new Request("http://localhost:3000/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, body }),
    }),
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("recording money through the proxy", () => {
  it("allows recording a payment against an invoice", async () => {
    const res = await post(`/api/dashboard/invoices/${INVOICE_ID}/payments`, {
      amount_paise: 4_200_000,
      method: "bank_transfer",
      reference: "UTR123",
      received_on: "2026-08-20",
    });
    expect(res.status).toBe(200);
  });

  it("allows reversing a recorded payment", async () => {
    const res = await post(`/api/dashboard/payments/${PAYMENT_ID}/reverse`, {
      reason: "cheque bounced",
    });
    expect(res.status).toBe(200);
  });

  it("allows matching an unmatched settlement to an invoice", async () => {
    const res = await post("/api/dashboard/exceptions/events/evt_abc.123/match", {
      invoice_id: INVOICE_ID,
    });
    expect(res.status).toBe(200);
  });

  it("allows reprocessing a stored customer reply", async () => {
    const res = await post(`/api/dashboard/exceptions/inbound/${INVOICE_ID}/retry`);
    expect(res.status).toBe(200);
  });

  it.each([
    // A collection endpoint is not a per-invoice one. Without the anchors, this
    // would let any suffix through.
    "/api/dashboard/invoices/payments",
    `/api/dashboard/invoices/${INVOICE_ID}/payments/../../admin`,
    "/api/dashboard/payments/reverse",
    `/api/dashboard/payments/${PAYMENT_ID}/reverse/extra`,
    // The event id is provider-supplied, so it is constrained rather than matched
    // with `.*` — an allowlist entry that accepts anything is not an allowlist.
    "/api/dashboard/exceptions/events/../../../admin/run-cycle/match",
    "/api/dashboard/exceptions/inbound/not-a-uuid/retry",
  ])("refuses %s", async (path) => {
    const res = await post(path);
    expect(res.status).toBe(400);
  });

  it("refuses every money path without a session", async () => {
    vi.resetModules();
    vi.doMock("@/lib/session", () => ({ currentSessionToken: vi.fn(async () => null) }));
    const res = await post(`/api/dashboard/invoices/${INVOICE_ID}/payments`);
    expect(res.status).toBe(401);
  });
});
