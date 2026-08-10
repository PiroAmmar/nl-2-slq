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
from pipeline.executor import _run_sql
from pipeline.embedding import call_embedding
from pipeline.llm import CallBudget, call_llm, BACKGROUND_JOB_SEMAPHORE

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/datasets", tags=["datasets"])

# Configurable via env var. Keep ≤6 to use a single LLM call (budget=2).
# Above 6 the generation is automatically split into chunks of 3 per call.
_GOLDEN_QUERY_COUNT = int(os.getenv("GOLDEN_QUERY_COUNT", "6"))
_GOLDEN_SYSTEM = """
You are a SQL expert. Generate {n} diverse golden SQL queries
for the following database schema. Cover: simple lookups, aggregations, filters,
group-bys, ordering, and edge cases (empty results, max/min, counts).

CRITICAL SQL RULES (USE SQLITE SYNTAX STRICTLY):
- ALWAYS wrap text column comparisons in LOWER(): LOWER(col) = LOWER('value')
- NEVER use bare equality for string filters: Channel = 'retail' -> LOWER(Channel) = 'retail'
- For LIKE patterns, use: LOWER(col) LIKE LOWER('%pattern%')
- Always add IS NOT NULL filters for aggregation columns.
- DO NOT use PostgreSQL `DATE 'YYYY-MM-DD'` syntax. Use standard strings: `col >= '2023-03-01'`.
- DO NOT use `FETCH FIRST`. Use `LIMIT n` to limit rows.

Schema:
{schema}

Reply using EXACTLY this plain-text delimited format. Do NOT use JSON. Do not include extra text.
===
Q: [Your first question here]
SQL: [Your first SQL query here]
===
Q: [Your second question here]
SQL: [Your second SQL query here]
===
"""




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


_MAX_COLS_PER_TABLE = 25  # Avoid flooding the prompt for very wide tables
_SAMPLE_VALUES = 2        # Distinct values per column shown in schema
_SAMPLE_MAX_LEN = 20      # Truncate long sample values


def _get_schema_with_samples(db_path: str, schema: dict[str, list[str]]) -> str:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    lines = []
    for table, cols in schema.items():
        lines.append(f"Table: {table}")
        shown_cols = cols[:_MAX_COLS_PER_TABLE]
        if len(cols) > _MAX_COLS_PER_TABLE:
            lines.append(f"  ... ({len(cols) - _MAX_COLS_PER_TABLE} more columns omitted)")
        for col in shown_cols:
            try:
                cursor.execute(f'SELECT DISTINCT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL LIMIT {_SAMPLE_VALUES};')
                vals = [str(r[0])[:_SAMPLE_MAX_LEN] for r in cursor.fetchall()]
                sample_str = f" (e.g. {', '.join(vals)})" if vals else ""
            except Exception:
                sample_str = ""
            lines.append(f"  - {col}{sample_str}")
    conn.close()
    return "\n".join(lines)


async def _generate_golden_bg(
    db_path: str,
    schema: dict,
    dataset_id: str,
    *,
    _attempt: int = 1,
) -> None:
    """
    Background task: generate + store golden queries, then mark golden_ready.

    On total failure (outer exception), schedules one retry after 30 s then gives up.

    NOTE: The retry is fire-and-forget via asyncio.create_task and is lost if the
    process restarts during the backoff window — acceptable since there is no
    job-persistence layer. Do NOT mistake this for a durable retry guarantee.
    """
    import re

    # Compute budget: ceil(_GOLDEN_QUERY_COUNT / 3) gen calls + 1 nl-batch call.
    if _GOLDEN_QUERY_COUNT <= 6:
        _n_gen_calls = 1
    else:
        _n_gen_calls = (_GOLDEN_QUERY_COUNT + 2) // 3  # ceiling division
    budget = CallBudget(max_calls=_n_gen_calls + 1)

    _total_failure = False

    async with BACKGROUND_JOB_SEMAPHORE:
        schema_text = _get_schema_with_samples(db_path, schema)
        try:
            # ── Chunked golden-query generation ───────────────────────────────────────────
            # When _GOLDEN_QUERY_COUNT ≤ 6: single call (original behaviour).
            # When > 6: split into chunks of 3 per call. Partial-chunk failure
            # is logged and skipped — surviving chunks are still used.
            # This matches the existing “drop invalid, keep valid” pattern used
            # in the SQL validation loop below.
            gqs: list[dict] = []
            if _GOLDEN_QUERY_COUNT <= 6:
                chunk_sizes = [_GOLDEN_QUERY_COUNT]
            else:
                chunk_sizes = []
                remaining = _GOLDEN_QUERY_COUNT
                while remaining > 0:
                    c = min(3, remaining)
                    chunk_sizes.append(c)
                    remaining -= c

            for n in chunk_sizes:
                try:
                    raw = await call_llm(
                        messages=[{"role": "user", "content": _GOLDEN_SYSTEM.format(n=n, schema=schema_text)}],
                        step="golden-gen",
                        budget=budget,
                        max_tokens=1200,
                        temperature=0.3,
                        priority="background",
                    )
                    blocks = raw.split("===")
                    for block in blocks:
                        block = block.strip()
                        if not block:
                            continue
                        match = re.search(r"Q:\s*(.*?)\nSQL:\s*(.*)", block, re.DOTALL | re.IGNORECASE)
                        if match:
                            gqs.append({
                                "question": match.group(1).strip(),
                                "sql": match.group(2).strip(),
                            })
                except Exception as chunk_exc:
                    # Partial failure: skip this chunk, keep surviving chunks.
                    logger.error(
                        "[golden-gen] Chunk of %d questions failed for dataset %s — skipping: %s",
                        n, dataset_id[:8], chunk_exc,
                    )

            if not gqs:
                logger.error(
                    "[golden-gen] Failed to parse any delimited output for dataset %s.",
                    dataset_id[:8],
                )

            # ── SQL validation loop ──────────────────────────────────────────────────
            valid_questions: list[str] = []
            valid_sqls: list[str] = []
            valid_answers: list[str] = []

            for g in gqs:
                if not isinstance(g, dict) or "question" not in g or "sql" not in g:
                    continue
                q = g["question"]
                sql = g["sql"]
                exec_res = _run_sql(sql, db_path)

                if exec_res.success and not exec_res.df.empty:
                    try:
                        # Format dataframe as a readable Markdown table
                        df_head = exec_res.df.head()
                        header = "| " + " | ".join(str(c) for c in df_head.columns) + " |"
                        sep = "|" + "|".join(["---"] * len(df_head.columns)) + "|"
                        rows = []
                        for _, row in df_head.iterrows():
                            rows.append("| " + " | ".join(str(v) for v in row.values) + " |")
                        answer_str = "\n".join([header, sep] + rows)
                    except Exception:
                        answer_str = str(exec_res.df.head())

                    valid_questions.append(q)
                    valid_sqls.append(sql)
                    valid_answers.append(answer_str)
                else:
                    if not exec_res.success:
                        logger.debug("[golden-gen] Dropping query due to error: %s", exec_res.error)
                    else:
                        logger.debug("[golden-gen] Dropping query because it returned 0 rows.")

            if not valid_questions:
                logger.error(
                    "[golden-gen] All generated golden queries were invalid or returned 0 rows for dataset %s",
                    dataset_id[:8],
                )
                session_store.update_golden_ready(dataset_id, True)
                return

            # ── NL batch conversion ───────────────────────────────────────────────────
            # Convert all markdown tables → natural language in ONE batched LLM call.
            # Use a numbered format ("1. answer") which is trivial to parse reliably.
            _NL_BATCH_SYSTEM = (
                "You are a helpful data analyst. I will give you a numbered list of questions "
                "and the data retrieved for each. Write a short, natural language answer for each. "
                "Do NOT show the table or explain SQL. Reply with ONLY numbered answers, one per line:\n"
                "1. [answer to question 1]\n2. [answer to question 2]\netc."
            )
            nl_user_parts = []
            for i, (q, a) in enumerate(zip(valid_questions, valid_answers), 1):
                nl_user_parts.append(f"{i}. Question: {q}\nData:\n{a}")
            nl_user_content = "\n\n".join(nl_user_parts)

            try:
                nl_raw = await call_llm(
                    messages=[
                        {"role": "system", "content": _NL_BATCH_SYSTEM},
                        {"role": "user", "content": nl_user_content},
                    ],
                    step="nl-gen-batch",
                    budget=budget,
                    max_tokens=600,
                    temperature=0.2,
                    priority="background",
                )
                # Parse "1. answer", "2. answer" ... lines
                nl_parsed = re.findall(r"^\d+\.\s+(.+)", nl_raw, re.MULTILINE)
                if len(nl_parsed) == len(valid_answers):
                    valid_answers = nl_parsed
                    logger.info("[golden-gen] NL batch conversion OK (%d answers)", len(nl_parsed))
                else:
                    logger.warning(
                        "[golden-gen] NL batch returned %d answers, expected %d — keeping markdown",
                        len(nl_parsed), len(valid_answers),
                    )
            except Exception as e:
                logger.error("[golden-gen] NL batch generation failed: %s — keeping markdown", e)

            # ── Embed + store ─────────────────────────────────────────────────────────
            embeddings = call_embedding(valid_questions)
            if embeddings is None:
                logger.error(
                    "[golden-gen] Embedding failed — cache not populated for dataset %s",
                    dataset_id[:8],
                )
                return

            golden_cache.store_golden(dataset_id, valid_questions, valid_sqls, valid_answers, embeddings)
            session_store.update_golden_ready(dataset_id, True)
            logger.info(
                "[golden-gen] %d valid golden queries stored for dataset %s",
                len(valid_questions), dataset_id[:8],
            )

        except Exception as exc:
            logger.error("[golden-gen] Total failure for dataset %s: %s", dataset_id[:8], exc)
            _total_failure = True

    # Retry logic OUTSIDE the semaphore so we don’t hold it during the 30s sleep.
    if _total_failure:
        if _attempt < 2:
            logger.info(
                "[golden-gen] Scheduling retry (attempt 2/2) in 30s for dataset %s",
                dataset_id[:8],
            )
            import asyncio as _asyncio
            await _asyncio.sleep(30)
            _asyncio.create_task(
                _generate_golden_bg(db_path, schema, dataset_id, _attempt=_attempt + 1)
            )
        else:
            logger.error(
                "[golden-gen] All 2 attempts failed for dataset %s — marking golden_ready=True with empty cache",
                dataset_id[:8],
            )
            session_store.update_golden_ready(dataset_id, True)


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
