/**
 * The work queue for things that failed.
 *
 * Two properties matter here and neither is cosmetic. Stuck customer replies must be
 * visible at all — before this they existed only in a database column, so a customer
 * writing "we already paid this" was silently dropped while the reminders continued.
 * And an unmatched payment must offer more than "Retry", because retrying cannot
 * conjure a payment link that was never in our database.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// The panel's buttons are client components that reach for the app router. Rendering
// them on the server for assertions needs it stubbed.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: () => {}, replace: () => {} }),
  usePathname: () => "/",
}));

import { ExceptionsPanel } from "@/components/Exceptions";
import type { Exceptions, QueueRow } from "@/lib/api";

function empty(): Exceptions {
  return { reconciliation: [], communication: [], unclosed_links: [], inbound: [], total: 0 };
}

const INBOUND = {
  id: "11111111-1111-1111-1111-111111111111",
  invoice_number: "INV-2048",
  sender: "abc@example.com",
  subject: "Re: INV-2048",
  excerpt: "We already paid this on the 14th, please check.",
  error: "RuntimeError: extractor exploded",
  attempts: 5,
  last_attempt_at: new Date().toISOString(),
  next_retry_at: null,
  exhausted: true,
  received_at: new Date().toISOString(),
};

const UNMATCHED = {
  id: "22222222-2222-2222-2222-222222222222",
  event_id: "evt_orphan_1",
  event_type: "payment_link.paid",
  invoice_number: null,
  amount_display: "₹42,000",
  error: "unmatched_payment",
  attempts: 6,
  last_attempt_at: new Date().toISOString(),
  next_retry_at: null,
  exhausted: true,
  received_at: new Date().toISOString(),
};

const INVOICE: QueueRow = {
  id: "33333333-3333-3333-3333-333333333333",
  invoice_number: "INV-2048",
  customer_name: "ABC Traders",
  amount_display: "₹42,000",
  outstanding_paise: 4_200_000,
  days_overdue: 18,
  status: "chasing",
  tier_label: "Tier 1",
  reason_category: "oversight",
  payment_url: null,
  next_action: "send",
  dispute_open: false,
  recovered_at: null,
  why: "",
  why_next: "",
  why_state: "chasing",
};

describe("ExceptionsPanel", () => {
  it("says nothing needs attention when nothing does", () => {
    const html = renderToStaticMarkup(<ExceptionsPanel data={empty()} />);
    expect(html).toContain("Nothing needs attention");
  });

  it("surfaces a customer reply that could not be understood", () => {
    const html = renderToStaticMarkup(
      <ExceptionsPanel data={{ ...empty(), inbound: [INBOUND], total: 1 }} />,
    );
    expect(html).toContain("Customer replies received but not understood");
    expect(html).toContain("We already paid this on the 14th");
    expect(html).toContain("Reprocess");
  });

  it("says plainly that a stuck reply has not been acted on", () => {
    // The dangerous reading is "we received it, so it was handled".
    const html = renderToStaticMarkup(
      <ExceptionsPanel data={{ ...empty(), inbound: [INBOUND], total: 1 }} />,
    );
    expect(html).toContain("has");
    expect(html).toContain("not");
    expect(html).toContain("been acted on yet");
  });

  it("counts exhausted inbound messages in the alarm", () => {
    const html = renderToStaticMarkup(
      <ExceptionsPanel data={{ ...empty(), inbound: [INBOUND], total: 1 }} />,
    );
    expect(html).toContain("out of automatic retries");
  });

  it("offers manual matching on an unmatched payment, not just a retry", () => {
    const html = renderToStaticMarkup(
      <ExceptionsPanel
        data={{ ...empty(), reconciliation: [UNMATCHED], total: 1 }}
        invoices={[INVOICE]}
      />,
    );
    expect(html).toContain("Match to invoice");
    expect(html).toContain("Retry");
  });

  it("hides the match control on an event that already has an invoice", () => {
    const html = renderToStaticMarkup(
      <ExceptionsPanel
        data={{
          ...empty(),
          reconciliation: [{ ...UNMATCHED, invoice_number: "INV-2048" }],
          total: 1,
        }}
        invoices={[INVOICE]}
      />,
    );
    expect(html).not.toContain("Match to invoice");
  });

  it("hides the match control when there are no invoices to match against", () => {
    const html = renderToStaticMarkup(
      <ExceptionsPanel data={{ ...empty(), reconciliation: [UNMATCHED], total: 1 }} />,
    );
    expect(html).not.toContain("Match to invoice");
  });
});
