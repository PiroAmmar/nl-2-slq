"""
backend/routers/datasets.py — Upload, schema, and golden-query status endpoints.

POST /datasets/upload   — receive file, build SQLite DB, fire golden-query gen
GET  /datasets/{id}     — return schema + golden_ready status
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File

from backend.schemas import DatasetStatusResponse, UploadResponse
from backend import session_store
from pipeline import cache as golden_cache
from pipeline.embedding import call_embedding
from pipeline.llm import CallBudget, call_llm

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/datasets", tags=["datasets"])

_GOLDEN_QUERY_COUNT = 10
_GOLDEN_SYSTEM = """You are a SQL expert. Generate {n} diverse golden SQL queries
for the following database schema. Cover: simple lookups, aggregations, filters,
group-bys, ordering, and edge cases (empty results, max/min, counts).

CRITICAL SQL RULES:
- ALWAYS wrap text column comparisons in LOWER(): LOWER(col) = LOWER('value')
- NEVER use bare equality for string filters: Channel = 'retail' -> LOWER(Channel) = 'retail'
- For LIKE patterns, use: LOWER(col) LIKE LOWER('%pattern%')
- Always add IS NOT NULL filters for aggregation columns.

Schema:
{schema}

Reply with ONLY valid JSON (no markdown):
{{"golden_queries": [{{"question": "...", "sql": "...", "answer": "..."}}]}}"""


def _get_schema(db_path: str) -> dict[str, list[str]]:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    schema: dict[str, list[str]] = {}
    for table in tables:
        cursor.execute(f"PRAGMA table_info('{table}');")
        schema[table] = [row[1] for row in cursor.fetchall()]
    conn.close()
    return schema


def _schema_to_text(schema: dict) -> str:
    return "\n".join(f"{t}({', '.join(cols)})" for t, cols in schema.items())


def _generate_golden_bg(db_path: str, schema: dict, dataset_id: str) -> None:
    """Background task: generate + store golden queries, then mark golden_ready."""
    budget = CallBudget(max_calls=2)
    schema_text = _schema_to_text(schema)
    try:
        raw = call_llm(
            messages=[{"role": "user", "content": _GOLDEN_SYSTEM.format(n=_GOLDEN_QUERY_COUNT, schema=schema_text)}],
            step="golden-gen",
            budget=budget,
            max_tokens=2048,
            temperature=0.3,
        )
        gqs = json.loads(raw).get("golden_queries", [])
    except Exception as exc:
        logger.error("[golden-gen] Failed for dataset %s: %s", dataset_id[:8], exc)
        return

    questions = [g["question"] for g in gqs]
    sqls = [g["sql"] for g in gqs]
    answers = [g["answer"] for g in gqs]

    embeddings = call_embedding(questions)
    if embeddings is None:
        logger.error("[golden-gen] Embedding failed — cache not populated for dataset %s", dataset_id[:8])
        return

    golden_cache.store_golden(dataset_id, questions, sqls, answers, embeddings)
    session_store.update_golden_ready(dataset_id, True)
    logger.info("[golden-gen] %d golden queries stored for dataset %s", len(gqs), dataset_id[:8])


@router.post("/upload", response_model=UploadResponse)
async def upload_dataset(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> UploadResponse:
    """
    Accept a CSV or Excel file, build a SQLite temp DB, register session,
    and fire golden-query generation as a background task.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in {".csv", ".xlsx", ".xls"}:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}. Use CSV or Excel.")

    file_bytes = await file.read()
    dataset_id = hashlib.sha256(file_bytes).hexdigest()

    # Idempotent: if same file already processed, return existing session
    existing = session_store.get_session(dataset_id)
    if existing and existing["golden_ready"]:
        schema = existing["schema"]
        return UploadResponse(
            dataset_id=dataset_id,
            n_tables=len(schema),
            table_names=list(schema.keys()),
            message="Dataset already loaded (cache hit).",
        )

    # Build SQLite DB
    import io
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        conn = sqlite3.connect(tmp.name)

        if ext == ".csv":
            df = pd.read_csv(io.BytesIO(file_bytes))
            table_name = os.path.splitext(file.filename)[0].replace(" ", "_").lower()
            df.to_sql(table_name, conn, if_exists="replace", index=False)
        else:
            sheets: dict = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None)
            for sheet_name, df_sheet in sheets.items():
                tbl = sheet_name.strip().replace(" ", "_").lower()
                for col in df_sheet.select_dtypes(include=["datetime64[ns]", "datetimetz"]).columns:
                    df_sheet[col] = df_sheet[col].dt.strftime("%Y-%m-%d %H:%M:%S")
                df_sheet.to_sql(tbl, conn, if_exists="replace", index=False)
        conn.close()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to parse file: {exc}")

    schema = _get_schema(tmp.name)
    session_store.set_session(
        dataset_id,
        {
            "db_path": tmp.name,
            "schema": schema,
            "dataset_hash": dataset_id,
            "golden_ready": False,
        },
    )

    # Fire golden-query generation in background (non-blocking)
    background_tasks.add_task(_generate_golden_bg, tmp.name, schema, dataset_id)

    return UploadResponse(
        dataset_id=dataset_id,
        n_tables=len(schema),
        table_names=list(schema.keys()),
        message="File uploaded. Golden queries generating in background.",
    )


@router.get("/{dataset_id}", response_model=DatasetStatusResponse)
async def get_dataset_status(dataset_id: str) -> DatasetStatusResponse:
    """Poll schema + golden_ready status."""
    entry = session_store.get_session(dataset_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found.")
    return DatasetStatusResponse(
        dataset_id=dataset_id,
        golden_ready=entry["golden_ready"],
        n_tables=len(entry["schema"]),
        tables=entry["schema"],
    )
