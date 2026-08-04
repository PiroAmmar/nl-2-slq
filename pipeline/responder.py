"""
pipeline/responder.py — Step 3.5: natural language answer + Plotly chart.

Chart type selected by deterministic rules first; LLM fallback only for
ambiguous shapes. Uses Groq for both NL answer and LLM fallback.
"""

from __future__ import annotations

import json
import logging

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from pipeline.executor import ExecutionResult
from pipeline.llm import CallBudget, call_llm

logger = logging.getLogger(__name__)

_ANSWER_SYSTEM = """You are a skilled data analyst writing a report for a business user.
Given a user question and query results, produce a rich, well-structured markdown response:

- Start with a short summary paragraph (2-3 sentences) highlighting the most important finding.
- Use **bold** for key numbers, dates, and entity names.
- If there are trends or notable patterns, describe them in a second paragraph.
- End with a concise bullet-point breakdown of key data points (max 8 bullets).
- Do NOT show raw SQL. Do NOT use code blocks.
- If the result set is empty, clearly explain no matching data was found and suggest why.
- Use PKR, %, or other units present in the data naturally in your text."""

_CHART_SYSTEM = """You are a data visualisation expert.
Given column names, dtypes, row count, and a small data sample, choose the
best chart type. Reply with ONLY valid JSON (no markdown):
{
  "chart_type": "bar" | "h_bar" | "line" | "multi_line" | "scatter" | "pie" | "heatmap" | "table_only",
  "x": "column_name_or_null",
  "y": "column_name_or_null",
  "color": "column_name_or_null",
  "reasoning": "one sentence"
}"""

LARGE_RESULT_THRESHOLD = 5_000  # rows above which scatter/line gets down-sampled


# ── public entry point ────────────────────────────────────────────────────────

def generate_response(
    question: str,
    result: ExecutionResult,
    budget: CallBudget,
) -> tuple[str, go.Figure | None, bool]:
    """
    Returns (nl_answer, plotly_figure_or_None, is_real_answer).
    figure is None when chart_type is table_only or chart build fails.
    is_real_answer is False when the LLM call failed and a generic
    placeholder was returned instead — callers should NOT cache that.
    """
    nl_answer, is_real_answer = _generate_answer(question, result, budget)
    fig = None

    if result.df is not None and not result.df.empty:
        decision = _select_chart_type(result.df, question, budget)
        if decision["chart_type"] != "table_only":
            fig = _build_chart(result.df, decision, question)

    return nl_answer, fig, is_real_answer


# ── NL answer ─────────────────────────────────────────────────────────────────

def _generate_answer(
    question: str,
    result: ExecutionResult,
    budget: CallBudget,
) -> tuple[str, bool]:
    if result.df is None or result.df.empty:
        preview = "Empty result set — no matching rows were found."
    else:
        preview = result.df.to_string(index=False)

    user_content = (
        f"Question: {question}\n\n"
        f"Row count: {result.row_count}\n"
        f"Result preview:\n{preview}"
    )
    try:
        text = call_llm(
            messages=[
                {"role": "system", "content": _ANSWER_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            step="responder",
            budget=budget,
            max_tokens=512,
            temperature=0.3,
        )
        return text, True
    except Exception as exc:
        logger.warning("Answer generation failed: %s — using fallback (not cached).", exc)
        return f"Retrieved {result.row_count} row(s). See the table below for details.", False


# ── chart type selection ──────────────────────────────────────────────────────

def _select_chart_type(
    df: pd.DataFrame,
    question: str,
    budget: CallBudget,
) -> dict:
    """
    Rule-based first; LLM fallback for ambiguous shapes.
    Returns a decision dict with keys: chart_type, x, y, color.
    """
    decision = _rule_based(df)
    if decision is not None:
        return decision
    # Fallback to LLM
    return _llm_chart_decision(df, question, budget)


def _rule_based(df: pd.DataFrame) -> dict | None:
    """
    Return a chart decision dict if rules fire, else None (→ LLM fallback).
    """
    cols = df.columns.tolist()
    n_rows = len(df)
    n_cols = len(cols)

    cat_cols = [c for c in cols if df[c].dtype == object or str(df[c].dtype) == "category"]
    num_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    date_cols = [
        c for c in cols
        if pd.api.types.is_datetime64_any_dtype(df[c])
        or (df[c].dtype == object and _looks_like_date(df[c]))
    ]

    # Single scalar
    if n_rows == 1 and n_cols == 1 and num_cols:
        return {"chart_type": "table_only", "x": None, "y": None, "color": None}

    # Time series: date + one numeric
    if date_cols and len(num_cols) == 1 and not cat_cols:
        return {"chart_type": "line", "x": date_cols[0], "y": num_cols[0], "color": None}

    # Multi-line: date + numeric + category
    if date_cols and num_cols and cat_cols:
        return {"chart_type": "multi_line", "x": date_cols[0], "y": num_cols[0], "color": cat_cols[0]}

    # Bar / horizontal bar: one categorical + one numeric
    if len(cat_cols) == 1 and len(num_cols) == 1:
        n_cats = df[cat_cols[0]].nunique()
        if n_cats <= 15:
            return {"chart_type": "bar", "x": cat_cols[0], "y": num_cols[0], "color": None}
        else:
            return {"chart_type": "h_bar", "x": num_cols[0], "y": cat_cols[0], "color": None}

    # Scatter: two numerics, many rows
    if len(num_cols) == 2 and n_rows > 1:
        return {"chart_type": "scatter", "x": num_cols[0], "y": num_cols[1], "color": None}

    # Heatmap: wide numeric table (many numeric cols, few rows)
    if len(num_cols) >= 4 and n_rows <= 30:
        return {"chart_type": "heatmap", "x": None, "y": None, "color": None}

    return None  # ambiguous → LLM fallback


def _looks_like_date(series: pd.Series) -> bool:
    try:
        pd.to_datetime(series.dropna().head(5))
        return True
    except Exception:
        return False


def _llm_chart_decision(df: pd.DataFrame, question: str, budget: CallBudget) -> dict:
    """LLM fallback for ambiguous shapes. Returns safe default on failure."""
    col_info = {c: str(df[c].dtype) for c in df.columns}
    sample = df.head(5).to_dict(orient="list")
    user_content = (
        f"Question: {question}\n"
        f"Row count: {len(df)}\n"
        f"Columns and dtypes: {json.dumps(col_info)}\n"
        f"Data sample: {json.dumps(sample, default=str)}"
    )
    try:
        raw = call_llm(
            messages=[
                {"role": "system", "content": _CHART_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            step="chart-selector",
            budget=budget,
            max_tokens=200,
            temperature=0.0,
        )
        parsed = json.loads(raw)
        # Validate columns exist
        for key in ("x", "y", "color"):
            if parsed.get(key) and parsed[key] not in df.columns:
                parsed[key] = None
        return parsed
    except Exception as exc:
        logger.warning("LLM chart decision failed: %s — using table_only", exc)
        return {"chart_type": "table_only", "x": None, "y": None, "color": None}


# ── chart rendering ───────────────────────────────────────────────────────────

def _build_chart(df: pd.DataFrame, decision: dict, question: str) -> go.Figure | None:
    """
    Build a Plotly figure from the decision dict.
    Falls back to None (table_only) if columns are invalid or build fails.
    """
    chart_type = decision.get("chart_type", "table_only")
    x = decision.get("x")
    y = decision.get("y")
    color = decision.get("color")
    title = question[:80]  # truncate long questions for chart title

    # Validate required columns exist
    for col in (x, y, color):
        if col and col not in df.columns:
            logger.warning("Chart column '%s' not in dataframe — skipping chart.", col)
            return None

    # Down-sample large frames for scatter/line
    if chart_type in ("scatter", "line", "multi_line") and len(df) > LARGE_RESULT_THRESHOLD:
        df = df.sample(LARGE_RESULT_THRESHOLD, random_state=42)
        logger.info("Down-sampled dataframe to %d rows for chart.", LARGE_RESULT_THRESHOLD)

    try:
        if chart_type == "bar":
            return px.bar(df, x=x, y=y, color=color, title=title)
        elif chart_type == "h_bar":
            return px.bar(df, x=x, y=y, color=color, orientation="h", title=title)
        elif chart_type == "line":
            return px.line(df, x=x, y=y, title=title)
        elif chart_type == "multi_line":
            return px.line(df, x=x, y=y, color=color, title=title)
        elif chart_type == "scatter":
            return px.scatter(df, x=x, y=y, color=color, title=title)
        elif chart_type == "pie":
            return px.pie(df, names=x, values=y, title=title)
        elif chart_type == "heatmap":
            num_df = df.select_dtypes(include="number")
            return px.imshow(num_df, title=title)
        else:
            return None
    except Exception as exc:
        logger.warning("Chart build failed (%s): %s — skipping chart.", chart_type, exc)
        return None
