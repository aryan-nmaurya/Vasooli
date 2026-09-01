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
    throw new Error(body.detail || body.error || `Request failed (${response.status})`);
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

export function liveGet<T>(path: string, merchantId: string) {
  return request<T>(path, { headers: { "X-Merchant-ID": merchantId } });
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
