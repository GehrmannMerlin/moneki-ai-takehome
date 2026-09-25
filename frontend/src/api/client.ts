import axios from "axios";

/** 与后端契约（docs/API_CONTRACT.md）一一对应的类型。 */

export interface Health {
  status: string;
  llm_mode: "live" | "mock";
  kb_docs: number;
  kb_chunks: number;
  valid_sales_rows: number;
  today: string;
  data_period: { start: string; end: string };
  cleaning_report: CleaningReport;
  index_key: string;
  kb_warnings: string[];
}

export interface CleaningReport {
  raw_rows: number;
  removed: Record<string, number>;
  kept_rows: number;
  kept_sales_rows: number;
  kept_refund_rows: number;
}

export interface Summary {
  start: string;
  end: string;
  store_id: string | null;
  product_id: string | null;
  net_revenue: number;
  refund_amount: number;
  orders: number;
  aov: number | null;
  qty: number;
}

export interface DailyPoint {
  date: string;
  net_revenue: number;
  orders: number;
  aov: number | null;
}

export interface TopProduct {
  product_id: string;
  product_name: string;
  product_category: string;
  net_revenue: number;
  orders: number;
  qty: number;
}

export interface MetaOptions {
  stores: { store_id: string; store_name: string }[];
  products: { product_id: string; product_name: string; product_category?: string }[];
  data_period: { start: string; end: string };
  today: string;
}

export interface Citation {
  doc_id: string;
  quote: string;
}

export interface DataEvidence {
  tool?: string;
  params?: Record<string, unknown>;
  sql?: string;
  result: unknown;
}

export type AnswerType = "data" | "doc" | "hybrid" | "refusal" | "clarify";

export interface ChatAnswer {
  answer: string;
  answer_type: AnswerType;
  citations: Citation[];
  data_evidence: DataEvidence[];
  trace_id: string;
}

export interface TraceStep {
  step: string;
  at_ms: number;
  took_ms: number | null;
  detail: unknown;
}

export interface TracePayload {
  trace_id: string;
  session_id: string | null;
  question: string;
  started_at: string;
  total_ms: number;
  steps: TraceStep[];
  llm_calls: unknown[];
  errors: { where: string; type: string; message: string; traceback: string }[];
}

export interface Filters {
  start: string;
  end: string;
  store_id: string;
  product_id: string;
}

const http = axios.create({ baseURL: "/", timeout: 200000 });

function query(filters: Filters): Record<string, string> {
  const params: Record<string, string> = { start: filters.start, end: filters.end };
  if (filters.store_id) params.store_id = filters.store_id;
  if (filters.product_id) params.product_id = filters.product_id;
  return params;
}

export const api = {
  health: () => http.get<Health>("/api/health").then((r) => r.data),
  metaOptions: () => http.get<MetaOptions>("/api/meta/options").then((r) => r.data),
  summary: (f: Filters) =>
    http.get<Summary>("/api/metrics/summary", { params: query(f) }).then((r) => r.data),
  daily: (f: Filters) =>
    http.get<{ days: DailyPoint[] }>("/api/metrics/daily", { params: query(f) }).then((r) => r.data),
  topProducts: (f: Filters, limit = 10) =>
    http
      .get<{ products: TopProduct[] }>("/api/metrics/top_products", {
        params: { ...query(f), limit },
      })
      .then((r) => r.data),
  dataQuality: () =>
    http
      .get<{ cleaning_report: CleaningReport; data_period: { start: string; end: string }; kb_warnings: string[] }>(
        "/api/data_quality",
      )
      .then((r) => r.data),
  chat: (sessionId: string, question: string) =>
    http.post<ChatAnswer>("/api/chat", { session_id: sessionId, question }).then((r) => r.data),
  trace: (traceId: string) => http.get<TracePayload>(`/api/trace/${traceId}`).then((r) => r.data),
};
