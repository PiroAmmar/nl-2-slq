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

from pipeline.executor import ExecutionResult
from pipeline.llm import CallBudget, call_llm

logger = logging.getLogger(__name__)

_ANSWER_SYSTEM = """You are a Business Intelligence analyst.
The SQL result is the ONLY source of truth.
Only draw conclusions directly supported by the SQL result.
Never explain WHY something happened unless the data explicitly proves it.
Never infer customer demand, marketing effectiveness, pricing strategy, product quality, market leadership, competitive advantage, or operational efficiency.
Use phrases like 'The data shows...' and 'Additional analysis would be required.'
If the data cannot answer the question, return confidence "REJECTED".

Structure your response EXACTLY as valid JSON matching this schema (do NOT use markdown blocks):
{
  "query_type": "lookup" | "ranking" | "comparison" | "trend" | "distribution" | "time_series" | "exploratory",
  "answer": {
    "title": "Short descriptive title (e.g. 'Highest Unit Price')",
    "value": "The primary answer (e.g. 'BrightWash')"
  },
  "evidence": [
    {"label": "Metric name", "value": "Metric value"}
  ],
  "confidence": "SUPPORTED" | "REJECTED",
  "limitations": "Optional string describing what the data cannot tell us"
}
"""



LARGE_RESULT_THRESHOLD = 5_000  # rows above which scatter/line gets down-sampled


# ── public entry point ────────────────────────────────────────────────────────

async def generate_response(
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
    nl_answer, is_real_answer = await _generate_answer(question, result, budget)
    fig = None

    if result.df is not None and not result.df.empty:
        decision = _select_chart_type(result.df, question, budget)
        if decision["chart_type"] != "table_only":
            fig = _build_chart(result.df, decision, question)

    return nl_answer, fig, is_real_answer


# ── NL answer ─────────────────────────────────────────────────────────────────

async def _generate_answer(
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
        f"SQL:\n{result.sql}\n\n"
        f"Columns and dtypes: {json.dumps(result.col_metadata)}\n\n"
        f"Row count: {result.row_count}\n"
        f"Result preview:\n{preview}"
    )
    try:
        draft = await call_llm(
            messages=[
                {"role": "system", "content": _ANSWER_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            step="responder",
            budget=budget,
            max_tokens=700,
            temperature=0.0,
        )
        final_text = draft
        # Strip markdown fences if LLM wrapped the JSON
        final_text = final_text.strip()
        import re
        final_text = re.sub(r"^```(?:json)?\s*", "", final_text, flags=re.IGNORECASE)
        final_text = re.sub(r"\s*```$", "", final_text)
        
        # Verify JSON
        json.loads(final_text)
        
        return final_text, True
    except Exception as exc:
        logger.warning("Answer generation failed: %s — using fallback (not cached).", exc)
        fallback = {
            "query_type": "exploratory",
            "answer": {"title": "Result", "value": f"Retrieved {result.row_count} row(s)."},
            "evidence": [],
            "confidence": "SUPPORTED"
        }
        return json.dumps(fallback), False


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
    # Fallback to table_only if rules fail
    return {"chart_type": "table_only", "x": None, "y": None, "color": None}


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

    golden_sequence = ["#ca8a04", "#eab308", "#facc15", "#fde047", "#a16207"]
    try:
        if chart_type == "bar":
            return px.bar(df, x=x, y=y, color=color, title=title, color_discrete_sequence=golden_sequence)
        elif chart_type == "h_bar":
            return px.bar(df, x=x, y=y, color=color, orientation="h", title=title, color_discrete_sequence=golden_sequence)
        elif chart_type == "line":
            return px.line(df, x=x, y=y, title=title, color_discrete_sequence=golden_sequence)
        elif chart_type == "multi_line":
            return px.line(df, x=x, y=y, color=color, title=title, color_discrete_sequence=golden_sequence)
        elif chart_type == "scatter":
            return px.scatter(df, x=x, y=y, color=color, title=title, color_discrete_sequence=golden_sequence)
        elif chart_type == "pie":
            return px.pie(df, names=x, values=y, title=title, color_discrete_sequence=golden_sequence)
        elif chart_type == "heatmap":
            num_df = df.select_dtypes(include="number")
            return px.imshow(num_df, title=title, color_continuous_scale="YlOrBr")
        else:
            return None
    except Exception as exc:
        logger.warning("Chart build failed (%s): %s — skipping chart.", chart_type, exc)
        return None
