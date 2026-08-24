/**
 * Backend client.
 *
 * Read endpoints are called directly. Mutating ones go through /app/api/action,
 * a Next route handler that holds the admin key server-side — a NEXT_PUBLIC_ variable
 * would ship the key in the browser bundle, where anyone can read it.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

/**
 * One fetch helper, two paths.
 *
 * On the server the backend is called directly with the admin key, which stays in the
 * Node process. In the browser the same logical call goes through /api/proxy, which
 * checks the session cookie before attaching any credential — so the client bundle
 * never contains a backend key and the backend is never a public read API.
 */
async function get<T>(path: string): Promise<T> {
  const onServer = typeof window === "undefined";

  const url = onServer ? `${API_BASE}${path}` : path.replace(/^\/api\//, "/api/proxy/");
  const headers: Record<string, string> = {};
  if (onServer) {
    const key = process.env.ADMIN_API_KEY;
    if (key) headers["X-Admin-Key"] = key;
  }

  const res = await fetch(url, { cache: "no-store", headers });
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
  /** Recovery is paused for an open dispute case. */
  dispute_open: boolean;
  why: string;
  why_next: string;
  why_state: string;
};

export type TimelineEntry = {
  at: string;
  actor: string;
  action: string;
  provenance: "ai" | "policy" | "razorpay" | "system" | "human";
  summary: string;
  detail: Record<string, unknown>;
};

export type ConversationKind =
  | "customer_message"
  | "system_message"
  | "ai_analysis"
  | "policy_decision"
  | "human_action"
  | "payment_event";

export type ConversationEntry = {
  at: string;
  kind: ConversationKind;
  speaker: string;
  headline: string;
  body: string | null;
  meta: Record<string, unknown>;
};

export type DisputeView = {
  id: string;
  status: string;
  is_open: boolean;
  reason: string;
  summary: string;
  facts: string[];
  confidence: number;
  confidence_display: string;
  source_excerpt: string;
  detected_by: string;
  ai_degraded: boolean;
  opened_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  resolution_note: string | null;
  recovery_resumed_at: string | null;
  next_action: string;
  payment_received_while_open: boolean;
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
  why: string;
  why_next: string;
  why_state: string;
  reply_count: number;
  last_reply_at: string | null;
  last_reply_excerpt: string | null;
  dispute: DisputeView | null;
  dispute_history: DisputeView[];
  reminders: ReminderView[];
  promises: PromiseView[];
  timeline: TimelineEntry[];
  conversation: ConversationEntry[];
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
export const getOpenDisputes = () => get<OpenDisputeRow[]>(`/api/dashboard/disputes`);

export type OpenDisputeRow = {
  case_id: string;
  invoice_id: string;
  invoice_number: string;
  customer_name: string;
  outstanding_display: string;
  reason: string;
  confidence_display: string;
  opened_at: string;
  detected_by: string;
};


export type ReconciliationException = {
  id: string;
  event_id: string;
  event_type: string;
  invoice_number: string | null;
  amount_display: string | null;
  error: string | null;
  attempts: number;
  last_attempt_at: string | null;
  next_retry_at: string | null;
  exhausted: boolean;
  received_at: string;
};

export type CommunicationException = {
  id: string;
  invoice_number: string;
  customer_name: string;
  tier: number;
  tone: string;
  error: string | null;
  attempts: number;
  last_attempt_at: string | null;
  next_retry_at: string | null;
  exhausted: boolean;
};

export type UnclosedLink = {
  id: string;
  invoice_number: string;
  payment_link_id: string;
  error: string | null;
  attempts: number;
  next_retry_at: string | null;
};

export type Exceptions = {
  reconciliation: ReconciliationException[];
  communication: CommunicationException[];
  unclosed_links: UnclosedLink[];
  total: number;
};

export const getExceptions = () => get<Exceptions>("/api/dashboard/exceptions");
