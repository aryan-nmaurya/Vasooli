"use client";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

export type LiveRegistration = {
  status: string;
  merchant_id: string;
  verification_token?: string | null;
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || body.error || `Request failed (${response.status})`);
  }
  return body as T;
}

export function registerLive(payload: {
  email: string;
  password: string;
  legal_business_name: string;
  country: string;
  timezone: string;
  accept_terms: boolean;
  accept_privacy: boolean;
}) {
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

export function liveGet<T>(path: string, merchantId: string) {
  return request<T>(path, { headers: { "X-Merchant-ID": merchantId } });
}

export function livePut<T>(path: string, merchantId: string, payload: unknown) {
  return request<T>(path, {
    method: "PUT",
    headers: { "X-Merchant-ID": merchantId },
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

export { API_BASE };
