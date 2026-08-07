"""
backend/routers/query.py — Ask a question about an uploaded dataset.

POST /query
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from backend.schemas import QueryRequest, QueryResponse
from backend import session_store
from pipeline.llm import CallBudget
from pipeline.embedding import call_embedding
from pipeline import cache as golden_cache

from pipeline.selector import select_tables_and_fields
from pipeline.generator import generate_sql
from pipeline.executor import execute_and_verify
from pipeline.responder import generate_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/query", tags=["query"])

_BUDGET_MAX_CALLS = 12


@router.post("", response_model=QueryResponse)
async def ask_question(req: QueryRequest) -> QueryResponse:
    """Run the full NL-to-SQL pipeline for one user question."""
    entry = session_store.get_session(req.dataset_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{req.dataset_id}' not found.")

    db_path: str = entry["db_path"]
    schema: dict = entry["schema"]
    dataset_hash: str = entry["dataset_hash"]
    question = req.question.strip()
    budget = CallBudget(max_calls=_BUDGET_MAX_CALLS)

    # ── Step 0: Semantic cache lookup ──────────────────────────────────────────
    question_embedding = call_embedding([question])
    if question_embedding is not None:
        hit = golden_cache.lookup(dataset_hash, question_embedding[0])
        if hit:
            return QueryResponse(
                answer=hit["answer"],
                sql=hit["sql"],
                cache_hit=True,
                similarity=hit["similarity"],
                source_type=hit.get("source_type"),
                source_pdf=hit.get("source_pdf"),
                source_page=hit.get("source_page"),
            )


    # ── Step 1/2: Triage + Selection ───────────────────────────────────────────
    try:
        status, selected_schema = await select_tables_and_fields(question, schema, budget)
        if status != "ok":
            return QueryResponse(
                answer=f"This question appears to be {status}. I can only answer questions related to the dataset.",
                cache_hit=False,
            )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # ── Step 3: SQL generation ─────────────────────────────────────────────────
    try:
        sql = await generate_sql(question, selected_schema, budget)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # ── Step 4: Execution + verification ───────────────────────────────────────
    async def _gen_fn(**kwargs):
        return await generate_sql(**kwargs)

    try:
        result = await execute_and_verify(
            question=question,
            initial_sql=sql,
            selected_schema=selected_schema,
            db_path=db_path,
            budget=budget,
            generate_sql_fn=_gen_fn,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not result.success:
        return QueryResponse(
            answer=result.error or "Query could not be completed. Please rephrase your question.",
            cache_hit=False,
        )

    # ── Step 5: NL response + chart ────────────────────────────────────────────
    try:
        nl_answer, fig, is_real_answer = await generate_response(question, result, budget)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    chart_json: str | None = None
    if fig is not None:
        chart_json = fig.to_json()

    # Cache real answers only (mistakes.md #16)
    if question_embedding is not None and result.success and is_real_answer:
        golden_cache.store_golden(
            dataset_hash,
            [question],
            [result.sql],
            [nl_answer],
            [question_embedding[0]],
        )

    return QueryResponse(
        answer=nl_answer,
        sql=result.sql,
        chart_json=chart_json,
        row_count=result.row_count,
        llm_calls_used=budget.used,
        cache_hit=False,
    )
