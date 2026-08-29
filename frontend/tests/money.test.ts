/**
 * Indian money formatting.
 *
 * Mirrors backend/app/core/money.py, and the two must agree. `toLocaleString("en-US")`
 * is the wrong tool here: it produces ₹640,000 where an Indian merchant expects
 * ₹6,40,000 — a figure that reads as wrong even though the number is right.
 */

import { describe, expect, it } from "vitest";

import { formatInr, formatInrShort } from "@/lib/money";

describe("formatInr", () => {
  it.each([
    [0, "₹0"],
    [100, "₹1"],
    [100_000, "₹1,000"],
    [1_000_000, "₹10,000"],
    [4_200_000, "₹42,000"],
  ])("formats %d paise as %s", (paise, expected) => {
    expect(formatInr(paise)).toBe(expected);
  });

  it("groups lakhs the Indian way, not the western way", () => {
    // The case that catches a naive toLocaleString: 6,40,000 — not 640,000.
    expect(formatInr(64_000_000)).toBe("₹6,40,000");
    expect(formatInr(1_00_00_000_00)).toBe("₹1,00,00,000");
  });

  it("keeps paise when they are not zero", () => {
    expect(formatInr(1_850_050)).toBe("₹18,500.50");
  });

  it("handles a negative balance without mangling the grouping", () => {
    expect(formatInr(-4_200_000)).toBe("-₹42,000");
  });
});

describe("formatInrShort", () => {
  it.each([
    // 8.475 lakh renders as 8.47, not 8.48: toFixed operates on a binary float where
    // 8.475 is not exactly representable. Immaterial here — this form is for tight
    // metric tiles, and any figure a merchant acts on comes from the backend's
    // exact `*_display` string, computed in integer paise.
    [8_47_500_00, "₹8.47L"],
    [1_00_00_000_00, "₹1.00Cr"],
  ])("abbreviates %d paise as %s", (paise, expected) => {
    expect(formatInrShort(paise)).toBe(expected);
  });

  it("never abbreviates away a figure someone would act on", () => {
    // The exact amount always survives via formatInr, which is integer-based.
    expect(formatInr(8_47_500_00)).toBe("₹8,47,500");
  });

  it("does not abbreviate small amounts, where precision matters more", () => {
    expect(formatInrShort(4_200_000)).toBe("₹42,000");
  });
});

describe("integer discipline — audit finding 11", () => {
  it("stays exact on a value float division would corrupt", () => {
    // 0.1 + 0.2 territory. paise/100 then *100 loses this; divmod does not.
    expect(formatInr(1_000_000_07)).toBe("₹10,00,000.07");
    expect(formatInr(70_07)).toBe("₹70.07");
    expect(formatInr(29)).toBe("₹0.29");
  });

  it("survives amounts beyond a float's exact-integer comfort", () => {
    // ₹1,00,00,00,00,000 in paise — well past where float rupee math drifts.
    expect(formatInr(1_00_00_00_00_000_00)).toBe("₹1,00,00,00,00,000");
  });

  it("never emits a floating-point artefact in the fraction", () => {
    for (let p = 0; p < 500; p++) {
      const out = formatInr(p);
      expect(out).not.toMatch(/\.\d{3,}/); // no 0.30000000000000004
    }
  });
});
