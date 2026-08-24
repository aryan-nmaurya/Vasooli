/**
 * Dashboard session tokens.
 *
 * The browser holds a signed token and nothing else; the backend admin key never
 * leaves the Next.js server. These tests cover the ways a forged or stale token could
 * otherwise be accepted.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

describe("session tokens", () => {
  beforeEach(() => {
    vi.resetModules();
    process.env.SESSION_SECRET = "a-test-secret-that-is-long-enough-to-sign-with";
    process.env.DASHBOARD_PASSWORD = "correct-horse-battery";
  });

  it("accepts a token it just minted", async () => {
    const { createToken, verifyToken } = await import("@/lib/session");
    expect(verifyToken(createToken())).toBe("operator");
  });

  it.each([
    ["undefined", undefined],
    ["empty", ""],
    ["not a token", "garbage"],
    ["missing signature", "v1.operator.9999999999"],
    ["wrong signature", "v1.operator.9999999999.deadbeef"],
    ["unknown version", "v2.operator.9999999999.sig"],
    ["non-numeric expiry", "v1.operator.soon.sig"],
  ])("rejects a %s token", async (_label, token) => {
    const { verifyToken } = await import("@/lib/session");
    expect(verifyToken(token)).toBeNull();
  });

  it("rejects a token whose expiry was extended", async () => {
    const { createToken, verifyToken } = await import("@/lib/session");
    const [version, subject, expires, signature] = createToken().split(".");
    const forged = `${version}.${subject}.${Number(expires) + 100_000}.${signature}`;
    // The expiry is inside the signed payload, so editing it invalidates the signature.
    expect(verifyToken(forged)).toBeNull();
  });

  it("rejects a token signed with a different secret", async () => {
    const { createToken } = await import("@/lib/session");
    const token = createToken();

    vi.resetModules();
    process.env.SESSION_SECRET = "an-entirely-different-secret-value-here";
    const { verifyToken } = await import("@/lib/session");

    // This is what rotating SESSION_SECRET does: everyone is signed out.
    expect(verifyToken(token)).toBeNull();
  });

  it("refuses to sign anything when the secret is unset", async () => {
    vi.resetModules();
    delete process.env.SESSION_SECRET;
    const { createToken } = await import("@/lib/session");
    // Failing loudly beats signing every session with the string "undefined".
    expect(() => createToken()).toThrow(/SESSION_SECRET/);
  });
});

describe("password check", () => {
  beforeEach(() => {
    vi.resetModules();
    process.env.DASHBOARD_PASSWORD = "correct-horse-battery";
  });

  it("accepts the right password", async () => {
    const { checkPassword } = await import("@/lib/session");
    expect(checkPassword("correct-horse-battery")).toBe(true);
  });

  it.each([
    ["wrong", "wrong-password-entirely"],
    ["a prefix of the real one", "correct-horse"],
    ["empty", ""],
  ])("rejects %s", async (_label, candidate) => {
    const { checkPassword } = await import("@/lib/session");
    expect(checkPassword(candidate)).toBe(false);
  });

  it("rejects everything when no password is configured", async () => {
    vi.resetModules();
    delete process.env.DASHBOARD_PASSWORD;
    const { checkPassword } = await import("@/lib/session");
    expect(checkPassword("")).toBe(false);
    expect(checkPassword("anything")).toBe(false);
  });
});
