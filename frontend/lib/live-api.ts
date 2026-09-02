"use client";

/**
 * Same-origin, always.
 *
 * These calls used to go straight to the backend, which made every one of them
 * subject to CORS — and CORS is configuration that is wrong by default, so sign-in
 * broke on `127.0.0.1` but not `localhost`, and would have broken entirely in any
 * deployment whose allowlist was not exactly right. `app/api/live/[...path]` forwards
 * server-side instead, where CORS does not apply. The path is deliberately identical
 * to the backend's so the refresh cookie's `path=/api/live/auth` still matches.
 */
const API_BASE = "";

export type LiveRegistration = {
  status: string;
  merchant_id: string;
  verification_token?: string | null;
};

export type LiveRegistrationPayload = {
  email: string;
  password: string;
  legal_business_name: string;
  country: string;
  timezone: string;
  accept_terms: boolean;
  accept_privacy: boolean;
};

/**
 * An API failure that still knows what the server said.
 *
 * The status was previously discarded, so a 402 from the payment gate arrived as an
 * anonymous red toast with no way to act on it — the merchant was told to choose a
 * plan and given nothing to click. Callers that care about a specific status can now
 * branch on it; everything else keeps treating this as an ordinary Error.
 */
export class LiveApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "LiveApiError";
    this.status = status;
  }
}

/**
 * Turn a FastAPI error body into a sentence a person can act on.
 *
 * A 422 answers with `detail` as an ARRAY of validation objects, which went into the
 * error message unchanged and reached the merchant as the literal text
 * "[object Object]" — most visibly on the signup form, where a rejected email address
 * is the single most likely thing to go wrong.
 */
function detailToMessage(detail: unknown, status: number): string {
  const fallback = `Request failed (${status})`;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) =>
        item && typeof item === "object" && typeof (item as { msg?: unknown }).msg === "string"
          ? (item as { msg: string }).msg
          : null,
      )
      .filter((msg): msg is string => Boolean(msg));
    if (messages.length) return messages.join(". ");
  }
  if (detail && typeof detail === "object") {
    const msg = (detail as { msg?: unknown }).msg;
    if (typeof msg === "string" && msg.trim()) return msg;
  }
  return fallback;
}

/** HTTP 402: the workspace has no active subscription. */
export function isPaymentRequired(cause: unknown): boolean {
  return cause instanceof LiveApiError && cause.status === 402;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    const headers = new Headers(init.headers);
    if (!(init.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: "include",
      headers,
    });
  } catch {
    // Same-origin now, so this is a genuine network failure rather than a blocked
    // preflight — the page itself could not be reached.
    throw new Error("Could not reach the server. Check your connection and try again.");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new LiveApiError(
      detailToMessage(body.detail ?? body.error, response.status),
      response.status,
    );
  }
  return body as T;
}

export function registerLive(payload: LiveRegistrationPayload) {
  return request<LiveRegistration>("/api/live/auth/register", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function verifyLive(token: string) {
  return request<{ status: string }>("/api/live/auth/verify-email", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export function verifyLiveCode(email: string, code: string) {
  return request<{ status: string }>("/api/live/auth/verify-email-code", {
    method: "POST",
    body: JSON.stringify({ email, code }),
  });
}

export function forgotPasswordLive(email: string) {
  return request<{ status: string; message: string; reset_token?: string | null }>("/api/live/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function resetPasswordLive(token: string, password: string) {
  return request<{ status: string }>("/api/live/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, password }),
  });
}

export function loginLive(email: string, password: string, otp?: string) {
  return request<{ merchants: string[]; user_id: string; status: string }>("/api/live/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password, ...(otp ? { otp } : {}) }),
  });
}

export function reauthLive(password: string) {
  return request<{ status: string; reauth_token: string }>("/api/live/auth/reauth/challenge", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export function logoutLive() {
  return request<{ status: string }>("/api/live/auth/logout", { method: "POST" });
}

export function liveGet<T>(
  path: string,
  merchantId: string,
  extraHeaders: Record<string, string> = {},
) {
  // Takes extra headers like the other verbs: the OAuth start endpoints are GETs
  // that still require a re-authentication token.
  return request<T>(path, { headers: { "X-Merchant-ID": merchantId, ...extraHeaders } });
}

export function livePut<T>(path: string, merchantId: string, payload: unknown, extraHeaders: Record<string, string> = {}) {
  return request<T>(path, {
    method: "PUT",
    headers: { "X-Merchant-ID": merchantId, ...extraHeaders },
    body: JSON.stringify(payload),
  });
}

export function livePost<T>(path: string, merchantId: string, payload?: unknown, extraHeaders: Record<string, string> = {}) {
  return request<T>(path, {
    method: "POST",
    headers: { "X-Merchant-ID": merchantId, ...extraHeaders },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
}

export function liveDelete<T>(path: string, merchantId: string) {
  return request<T>(path, { method: "DELETE", headers: { "X-Merchant-ID": merchantId } });
}

export function liveUpload<T>(path: string, merchantId: string, form: FormData) {
  return request<T>(path, {
    method: "POST",
    headers: { "X-Merchant-ID": merchantId },
    body: form,
  });
}

export async function liveDownload(path: string, merchantId: string): Promise<Blob> {
  const response = await fetch(path, {
    credentials: "include",
    headers: { "X-Merchant-ID": merchantId },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Download failed (${response.status})`);
  }
  return response.blob();
}

export { API_BASE };
