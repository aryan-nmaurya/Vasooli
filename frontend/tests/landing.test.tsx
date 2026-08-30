import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Landing } from "@/components/Landing";

describe("Landing", () => {
  it("explains the product, its safety boundary, proof, and honest scope", () => {
    const html = renderToStaticMarkup(<Landing />);

    expect(html).toContain("Recover revenue.");
    expect(html).toContain("AI reads.");
    expect(html).toContain("Policy decides.");
    expect(html).toContain("Razorpay verifies.");
    expect(html).toContain("65.1%");
    expect(html).toContain("Real workflows.");
    expect(html).toContain("Test money.");
  });

  it("offers clear paths into the demo, explanation, guide, and evidence", () => {
    const html = renderToStaticMarkup(<Landing />);

    expect(html).toContain('href="/login"');
    expect(html).toContain('href="#how"');
    expect(html).toContain('href="/guide"');
    expect(html).toContain("github.com/aryan-nmaurya/Vasooli");
  });

  // ---------------------------------------------------------------------
  // Claims the code cannot support.
  //
  // A landing page is the easiest place in a codebase for a promise to drift ahead
  // of the implementation, and the most expensive place for a reviewer to catch it.
  // These pin the specific overclaims an audit found and the corrections that
  // replaced them, so re-introducing one fails a test rather than a demo.
  // ---------------------------------------------------------------------

  it("does not claim to discover invoices it can only be given", () => {
    const html = renderToStaticMarkup(<Landing />);

    expect(html).not.toContain("finds overdue Razorpay invoices");
    expect(html).toContain("You import your overdue invoices");
    expect(html).toContain("does not browse your Razorpay account");
  });

  it("does not promise an instantaneous stop it cannot guarantee", () => {
    const html = renderToStaticMarkup(<Landing />);

    expect(html).not.toContain("stops the instant");
    expect(html).toContain("stops as soon as the payment is");
  });

  it("acknowledges money that arrives outside a Vasooli payment link", () => {
    const html = renderToStaticMarkup(<Landing />);

    expect(html).toContain("bank transfer");
    expect(html).toContain("their assertion");
  });

  it("labels the recovery figure as a simulation, and does not lead with it", () => {
    const html = renderToStaticMarkup(<Landing />);

    expect(html).toContain("Simulated, not observed");
    expect(html).toContain("No merchant has run on this");
    // The verified metrics come first in document order.
    expect(html.indexOf("tests passing")).toBeLessThan(html.indexOf("65.1%"));
  });

  it("discloses the workload the simulation still handed to a person", () => {
    const html = renderToStaticMarkup(<Landing />);
    expect(html).toContain("83 of the 150");
  });

  it("states what the system does not do", () => {
    const html = renderToStaticMarkup(<Landing />);

    expect(html).toContain("no connector to your accounting system");
    expect(html).toContain("does not ingest Razorpay refunds or chargebacks");
    expect(html).toContain("production-shaped prototype");
  });
});
