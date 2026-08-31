import { describe, expect, it } from "vitest";

import { shellForPath } from "@/components/AppShell";

describe("application shell routing", () => {
  it("never puts live routes in the guided demo shell", () => {
    expect(shellForPath("/live", true)).toBe("live");
    expect(shellForPath("/live/invoices", true)).toBe("live");
  });

  it("keeps live login in the public authentication shell", () => {
    expect(shellForPath("/live/login", true)).toBe("public");
    expect(shellForPath("/live/login/", true)).toBe("public");
  });

  it("uses the demo shell only for authenticated demo routes", () => {
    expect(shellForPath("/", true)).toBe("demo");
    expect(shellForPath("/audit", true)).toBe("demo");
    expect(shellForPath("/audit", false)).toBe("public");
  });
});
