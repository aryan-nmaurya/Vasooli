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
});
