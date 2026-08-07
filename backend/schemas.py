"""
backend/schemas.py — Pydantic request/response models.

These are the API contract agreed on between backend routers and the
React frontend. Do not change field names without updating both sides.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


# ── Dataset endpoints ─────────────────────────────────────────────────────────

class UploadResponse(BaseModel):
    dataset_id: str
    n_tables: int
    table_names: list[str]
    message: str


class DatasetStatusResponse(BaseModel):
    dataset_id: str
    golden_ready: bool
    n_tables: int
    tables: dict[str, list[str]]   # renamed from 'schema' to avoid BaseModel shadow


# ── Query endpoint ────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    dataset_id: str
    question: str


class QueryResponse(BaseModel):
    answer: str
    sql: Optional[str] = None
    chart_json: Optional[str] = None       # fig.to_json() from Plotly
    row_count: Optional[int] = None
    llm_calls_used: Optional[int] = None
    cache_hit: bool = False
    similarity: Optional[float] = None
    # doc-QA deep-link fields (only present on doc_qa cache hits)
    source_type: Optional[str] = None      # "doc_qa" | "golden_query" | None
    source_pdf: Optional[str] = None
    source_page: Optional[int] = None


# ── Dashboard endpoint ────────────────────────────────────────────────────────

class DashboardResponse(BaseModel):
    dataset_id: str
    golden_ready: bool
    entries: list[dict]                    # [{question, sql, answer, ...}]


# ── Doc-QA endpoints ──────────────────────────────────────────────────────────

class DocQAIngestRequest(BaseModel):
    dataset_id: str
    docx_filename: str                     # filename within input_docs/


class DocQAIngestResponse(BaseModel):
    job_id: str
    message: str


class DocQAStatusResponse(BaseModel):
    job_id: str
    status: str                            # "queued" | "running" | "done" | "failed" | "cancelled"
    total: Optional[int] = None
    success_count: Optional[int] = None
    failure_count: Optional[int] = None
    pdf_path: Optional[str] = None
    failures: Optional[list[dict]] = None
    error: Optional[str] = None
