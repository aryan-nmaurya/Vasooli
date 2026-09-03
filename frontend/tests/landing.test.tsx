import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Landing } from "@/components/Landing";

describe("production landing page", () => {
  it("explains the complete controlled recovery loop", () => {
    const html = renderToStaticMarkup(<Landing />);
    expect(html).toContain("Turn overdue invoices");
    expect(html).toContain("into accountable action");
    expect(html).toContain("Connect your financial system");
    expect(html).toContain("Detect what needs attention");
    expect(html).toContain("Communicate with context");
    expect(html).toContain("Reconcile and stop safely");
    expect(html).toContain("Customer text and model output can never mark an invoice paid");
  });

  it("names only implemented ERP connection contracts", () => {
    const html = renderToStaticMarkup(<Landing />);
    expect(html).toContain("Zoho Books");
    expect(html).toContain("Previewed ledger import");
    expect(html).toContain("Signed webhook ingestion");
    expect(html).not.toContain("Tally");
  });

  it("does not promise a card-free trial the product no longer offers", () => {
    const html = renderToStaticMarkup(<Landing />);
    // The trial begins by confirming an Autopay mandate, which takes a refundable ₹2.
    // Saying "No card required" and then asking for one is the kind of thing a merchant
    // discovers at the worst moment.
    expect(html).not.toContain("No card required");
    expect(html).toContain("₹2 to start, refunded");
  });

  it("shows measured evidence rather than only claims", () => {
    const html = renderToStaticMarkup(<Landing />);
    // Figures come from backend/eval/out/results.csv. Pinned so a future edit cannot
    // quietly round them into something the harness does not support.
    expect(html).toContain("1.1");
    expect(html).toContain("Contacts per invoice");
    expect(html).toContain("Compliance breaches");
    expect(html).toContain("98.7%");
    // The unflattering number stays. Dropping it would turn measurement into marketing.
    expect(html).toContain("65%");
    expect(html).not.toContain("any ERP");
  });

  it("keeps calls to action focused on merchant accounts", () => {
    const html = renderToStaticMarkup(<Landing />);
    expect(html).toContain('href="/register"');
    expect(html).toContain('href="/live/login"');
    expect(html).toContain('href="/pricing"');
  });

  /**
   * The page must not *position* Vasooli as a demo — the prose sells a product to a
   * merchant, and words like "simulated" or "hackathon" undercut that.
   *
   * Navigation is a separate question, and conflating the two removed the only way in
   * for the third audience this page serves. A prospective merchant registers or signs
   * in; a reviewer or mentor arrives holding no credential at all, and for a while
   * every link led to `/register` or `/live/login`. A working product looked like a
   * locked door, which is the opposite of what the positioning was protecting.
   *
   * So the rule applies to the copy, not the links: anchors are stripped before the
   * scan, and a labelled entry point is allowed to say plainly where it goes. Telling
   * a reviewer the door is a demo is honest; describing the product as one is not.
   */
  const prose = () =>
    renderToStaticMarkup(<Landing />)
      .replace(/<a\b[^>]*>[\s\S]*?<\/a>/gi, " ")
      .toLowerCase();

  it.each(["simulated", "seeded", "time machine", "sample data", "test mode", "hackathon", "fake integration"])("does not position the product as %s", (term) => {
    expect(prose()).not.toContain(term);
  });

  it("keeps demo and reviewer wording out of the prose", () => {
    // Allowed inside an anchor label, forbidden in the surrounding copy.
    expect(prose()).not.toContain("demo");
    expect(prose()).not.toContain("reviewer");
    expect(prose()).not.toContain("mentor");
  });

  it("gives a credential-less reviewer a way in", () => {
    const html = renderToStaticMarkup(<Landing />);
    expect(html).toContain('href="/login"');
    expect(html).toContain("Explore the product");
    expect(html).not.toContain('href="/guide"');
  });
});
