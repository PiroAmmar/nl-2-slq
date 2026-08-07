"""
pipeline/executor.py — Step 3.4: execute, retry, and verify SQL.

State machine:
  generate → execute → [retry loop on failure] → verify → [regen loop if wrong]

ExecutionResult carries everything downstream (3.5 + visualization) needs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field

import pandas as pd

from pipeline.llm import CallBudget, call_llm

logger = logging.getLogger(__name__)

MAX_EXECUTION_RETRIES = 3   # retry loop cap (syntax/schema errors)
QUERY_TIMEOUT_MS = 12_000   # 12 s
ROW_LIMIT = 500


@dataclass
class ExecutionResult:
    success: bool
    sql: str
    df: pd.DataFrame | None = None
    row_count: int = 0
    col_metadata: dict[str, str] = field(default_factory=dict)  # col_name → dtype
    error: str | None = None
    error_type: str | None = None   # syntax | schema | timeout | permission | unknown
    attempts: int = 0
    cache_hit: bool = False


# ── public entry point ────────────────────────────────────────────────────────

async def execute_and_verify(
    question: str,
    initial_sql: str,
    selected_schema: dict[str, list[str]],
    db_path: str,
    budget: CallBudget,
    # Pass generator coroutine function to avoid circular import
    generate_sql_fn,  # async Callable
) -> ExecutionResult:
    """
    Full execution + verification loop.
    Returns ExecutionResult with .success=True on verified success,
    or .success=False with .error describing what went wrong.
    """
    sql = initial_sql
    exec_attempts = 0
    need_reselect = False  # flag: schema error → caller should re-run selector

    # ── execution + retry loop ────────────────────────────────────────────────
    while exec_attempts <= MAX_EXECUTION_RETRIES:
        exec_attempts += 1
        # sqlite I/O is blocking — offload to a worker thread so it never
        # stalls the event loop when many questions run concurrently.
        result = await asyncio.to_thread(_run_sql, sql, db_path)
        result.attempts = exec_attempts

        if not result.success:
            logger.warning(
                "Execution failed (attempt %d/%d) [%s]: %s",
                exec_attempts, MAX_EXECUTION_RETRIES, result.error_type, result.error,
            )

            if exec_attempts > MAX_EXECUTION_RETRIES:
                result.error = (
                    f"Query could not be completed after {MAX_EXECUTION_RETRIES} attempts. "
                    f"Last error: {result.error}"
                )
                return result

            # Schema errors: flag but still try correction (full re-select is caller's job)
            sql = await generate_sql_fn(
                question=question,
                selected_schema=selected_schema,
                budget=budget,
                prior_sql=sql,
                error_message=result.error,
            )
            continue

        # ── if we reach here, execution succeeded ─────────────────────────────
        logger.info("Execution succeeded. Row count: %d", result.row_count)
        return result

    # Should not reach here
    return ExecutionResult(
        success=False,
        sql=sql,
        error="Unexpected loop exit.",
        attempts=exec_attempts,
    )


# ── internal helpers ──────────────────────────────────────────────────────────

def _run_sql(sql: str, db_path: str) -> ExecutionResult:
    """Execute SQL against a read-only SQLite connection."""
    sql = _inject_limit(sql)

    try:
        # Static BI Validation
        sql_upper = sql.upper()
        if re.search(r"AVG\s*\([^)]+/[^)]+\)", sql_upper):
            raise ValueError("CRITICAL BI ERROR: Calculated average of a ratio (e.g. AVG(A/B)). You must calculate the ratio of the sums: SUM(A)/SUM(B).")

        # Read-only URI
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=QUERY_TIMEOUT_MS / 1000)
        conn.execute(f"PRAGMA busy_timeout = {QUERY_TIMEOUT_MS};")

        df = pd.read_sql_query(sql, conn)
        conn.close()

        col_meta = {col: str(df[col].dtype) for col in df.columns}
        return ExecutionResult(
            success=True,
            sql=sql,
            df=df,
            row_count=len(df),
            col_metadata=col_meta,
        )

    except Exception as exc:
        err_str = str(exc)
        error_type = _classify_error(err_str)
        return ExecutionResult(
            success=False,
            sql=sql,
            error=err_str,
            error_type=error_type,
        )


def _inject_limit(sql: str) -> str:
    """Add LIMIT 500 if no LIMIT clause present."""
    if not re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
        sql = sql.rstrip().rstrip(";")
        sql += f" LIMIT {ROW_LIMIT};"
    return sql


def _classify_error(err: str) -> str:
    err_lower = err.lower()
    if "no such table" in err_lower or "no such column" in err_lower:
        return "schema"
    if "syntax error" in err_lower:
        return "syntax"
    if "timeout" in err_lower or "busy" in err_lower:
        return "timeout"
    if "permission" in err_lower or "readonly" in err_lower or "read-only" in err_lower:
        return "permission"
    return "unknown"



