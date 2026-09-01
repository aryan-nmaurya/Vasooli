import { describe, expect, it } from "vitest";

import { nextRecoveryCycle } from "@/lib/recovery-schedule";

describe("nextRecoveryCycle", () => {
  it("uses today's 10:00 IST cycle before the cutoff", () => {
    expect(nextRecoveryCycle(new Date("2026-09-01T03:00:00Z")).toISOString()).toBe("2026-09-01T04:30:00.000Z");
  });

  it("uses tomorrow's 10:00 IST cycle after the cutoff", () => {
    expect(nextRecoveryCycle(new Date("2026-09-01T10:00:00Z")).toISOString()).toBe("2026-09-02T04:30:00.000Z");
  });
});
