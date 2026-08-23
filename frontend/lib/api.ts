/**
 * Backend client.
 *
 * Read endpoints are called directly. Mutating ones go through /app/api/action,
 * a Next route handler that holds the admin key server-side — a NEXT_PUBLIC_ variable
 * would ship the key in the browser bundle, where anyone can read it.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

async function get<T>(path: string, revalidate = 0): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    // The demo turns on payments live; a cached overview would show stale totals.
    cache: "no-store",
    next: { revalidate },
  });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export type Overview = {
  total_overdue_display: string;
  total_overdue_paise: number;
  recovered_display: string;
  recovered_paise: number;
  recovery_rate: number;
  recovery_rate_display: string;
  avg_days_to_recovery: number | null;
  automation_rate_display: string;
  invoices_total: number;
  invoices_recovered: number;
  invoices_in_human_review: number;
  active_promises: number;
  broken_promises: number;
  counts_by_status: Record<string, number>;
  counts_by_reason: Record<string, number>;
};

export type QueueRow = {
  id: string;
  invoice_number: string;
  customer_name: string;
  amount_display: string;
  outstanding_paise: number;
  days_overdue: number;
  status: string;
  tier_label: string;
  reason_category: string | null;
  payment_url: string | null;
  next_action: string;
};

export type TimelineEntry = {
  at: string;
  actor: string;
  action: string;
  provenance: "ai" | "policy" | "razorpay" | "system" | "human";
  summary: string;
  detail: Record<string, unknown>;
};

export type ReminderView = {
  tier: number;
  tone: string;
  subject: string;
  body: string;
  generated_by: string;
  llm_degraded: boolean;
  sent_at: string | null;
  policy_rendered: string | null;
};

export type PromiseView = {
  id: string;
  invoice_number: string;
  customer_name: string;
  promised_date: string;
  amount_display: string;
  status: string;
  confidence: number;
  tier_at_pause: number;
  excerpt: string;
};

export type InvoiceDetail = {
  id: string;
  invoice_number: string;
  customer_name: string;
  customer_email: string;
  amount_display: string;
  paid_display: string;
  outstanding_display: string;
  status: string;
  days_overdue: number;
  due_at: string;
  reason_category: string | null;
  reason_explanation: string | null;
  reason_confidence: number | null;
  reason_llm_disagreed: boolean;
  reminders_sent: number;
  current_tier: number;
  escalated_to_human_at: string | null;
  escalation_reason: string | null;
  recovered_at: string | null;
  payment_url: string | null;
  payment_link_status: string | null;
  reminders: ReminderView[];
  promises: PromiseView[];
  timeline: TimelineEntry[];
};

export type AuditEntry = {
  at: string;
  invoice_number: string | null;
  actor: string;
  action: string;
  provenance: TimelineEntry["provenance"];
  summary: string;
  detail: Record<string, unknown>;
};

export const getOverview = (days = 30) => get<Overview>(`/api/dashboard/overview?days=${days}`);
export const getQueue = (qs = "") => get<QueueRow[]>(`/api/dashboard/queue?limit=200${qs}`);
export const getInvoice = (id: string) => get<InvoiceDetail>(`/api/dashboard/invoices/${id}`);
export const getPromises = (status?: string) =>
  get<PromiseView[]>(`/api/dashboard/promises${status ? `?status=${status}` : ""}`);
export const getAudit = (qs = "") => get<AuditEntry[]>(`/api/dashboard/audit?limit=200${qs}`);
