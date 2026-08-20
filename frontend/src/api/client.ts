// src/api/client.ts — Typed fetch wrappers for every backend endpoint

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// ── Types (mirrors backend/schemas.py) ────────────────────────────────────────

export interface UploadResponse {
  dataset_id: string;
  n_tables: number;
  table_names: string[];
  message: string;
}

export interface DatasetStatusResponse {
  dataset_id: string;
  golden_ready: boolean;
  n_tables: number;
  tables: Record<string, string[]>;
}

export interface QueryRequest {
  dataset_id: string;
  question: string;
}

export interface QueryResponse {
  answer: string;
  sql?: string;
  chart_json?: string;
  row_count?: number;
  llm_calls_used?: number;
  cache_hit: boolean;
  similarity?: number;
  source_type?: "doc_qa" | "golden_query" | null;
  source_pdf?: string | null;
  source_page?: number | null;
}

export interface DashboardEntry {
  question: string;
  sql: string;
  answer: string;
  source_type: string;
  source_pdf?: string | null;
  source_page?: number | null;
  role?: string | null;
  section?: string | null;
  similarity: number;
}

export interface DashboardResponse {
  dataset_id: string;
  golden_ready: boolean;
  entries: DashboardEntry[];
}

export interface DocQAIngestResponse {
  job_id: string;
  message: string;
}

export interface DocQAStatusResponse {
  job_id: string;
  status: "queued" | "running" | "done" | "failed";
  total?: number;
  success_count?: number;
  failure_count?: number;
  pdf_path?: string | null;
  failures?: Array<{ question: string; reason: string; role: string; section: string }>;
  error?: string;
}

// ── Helper ────────────────────────────────────────────────────────────────────

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ── API functions ─────────────────────────────────────────────────────────────

export const api = {
  uploadDataset: async (file: File): Promise<UploadResponse> => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${BASE_URL}/datasets/upload`, { method: "POST", body: fd });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(body.detail ?? `HTTP ${res.status}`);
    }
    return res.json();
  },

  getDatasetStatus: (datasetId: string): Promise<DatasetStatusResponse> =>
    request(`/datasets/${datasetId}`),

  askQuestion: (req: QueryRequest): Promise<QueryResponse> =>
    request("/query", { method: "POST", body: JSON.stringify(req) }),

  getDashboard: (datasetId: string): Promise<DashboardResponse> =>
    request(`/dashboard/${datasetId}`),

  startDocQAIngest: (datasetId: string, docxFilename: string): Promise<DocQAIngestResponse> =>
    request("/doc_qa/ingest", {
      method: "POST",
      body: JSON.stringify({ dataset_id: datasetId, docx_filename: docxFilename }),
    }),

  getDocQAStatus: (jobId: string): Promise<DocQAStatusResponse> =>
    request(`/doc_qa/status/${jobId}`),
};
