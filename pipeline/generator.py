"""
pipeline/generator.py — Step 3.3: SQL generation grounded by sql-guide skill.

Uses Groq. Injects sql_guide_skill.md into the system prompt so every
generated query follows established conventions, dialect rules, and safety
requirements. Same system prompt is reused in retry/correction calls.
"""

from __future__ import annotations

import logging
import os
import re

from pipeline.llm import CallBudget, call_llm

logger = logging.getLogger(__name__)

_SKILL_PATH = os.getenv(
    "SQL_GUIDE_PATH",
    r"C:\Users\Syed Ammar Ali\.gemini\config\skills\sql-guide\SKILL.md",
)
try:
    with open(_SKILL_PATH, encoding="utf-8") as f:
        _SQL_GUIDE = f.read()
except FileNotFoundError:
    _SQL_GUIDE = ""
    logger.warning("sql_guide_skill.md not found — SQL generation will run without it.")

_SYSTEM = f"""You are an expert SQL developer specialising in SQLite.
Generate SQL queries that precisely answer the user's question using ONLY the
tables and columns provided in the schema. Follow ALL rules below.

--- SQL GUIDE ---
{_SQL_GUIDE}
--- END SQL GUIDE ---

Output rules:
- Return ONLY the raw SQL query — no markdown fences, no commentary, no explanation.
- Read-only queries only (SELECT). Never INSERT/UPDATE/DELETE/DROP/ALTER/CREATE.
- Always include LIMIT 500 unless the question implies a single-row result.
- Use double-quoted identifiers only when the column name conflicts with SQL keywords.
- Parameterised values only — no string concatenation.

String comparison rules (CRITICAL — prevents silent empty results):
- ALWAYS wrap text column comparisons in LOWER(): LOWER(col) = LOWER('value')
- NEVER use bare equality for string filters: Channel = 'retail' → LOWER(Channel) = 'retail'
- For LIKE patterns, use: LOWER(col) LIKE LOWER('%pattern%')
- For IN lists, use: LOWER(col) IN ('value1', 'value2')

Date/time rules:
- SQLite stores dates as TEXT (ISO 8601: 'YYYY-MM-DD HH:MM:SS') or as REAL (Julian day).
- Use strftime('%Y-%m-%d', col) to normalise before comparing: strftime('%Y-%m', date_col) = '2025-01'
- For year/month extraction always use strftime, not YEAR() or MONTH() (those don't exist in SQLite).
- When filtering a date range use: date_col BETWEEN '2025-01-01' AND '2025-12-31'

NULL safety rules:
- Always add IS NOT NULL filters for aggregation columns (AVG, SUM, MAX, MIN).
- Use COALESCE(col, 0) when a zero default is appropriate.
- Use COUNT(*) for row counts, COUNT(col) only when NULLs should be excluded.
"""


def generate_sql(
    question: str,
    selected_schema: dict[str, list[str]],
    budget: CallBudget,
    *,
    prior_sql: str | None = None,
    error_message: str | None = None,
    verifier_reasoning: str | None = None,
) -> str:
    """
    Generate (or correct) a SQL query.

    When prior_sql + error_message are provided: correction mode (retry loop).
    When prior_sql + verifier_reasoning are provided: regeneration mode.
    Returns cleaned SQL string.
    """
    schema_text = _schema_to_text(selected_schema)

    if prior_sql and error_message:
        # Correction: execution failed
        user_content = (
            f"The following SQL query failed to execute:\n\n"
            f"```sql\n{prior_sql}\n```\n\n"
            f"Database error:\n{error_message}\n\n"
            f"Schema:\n{schema_text}\n\n"
            f"Original question: {question}\n\n"
            "Fix the SQL query so it executes successfully and answers the question. "
            "Return ONLY the corrected SQL."
        )
        step = "generator-correction"
    elif prior_sql and verifier_reasoning:
        # Regeneration: execution succeeded but result was wrong
        user_content = (
            f"The following SQL executed successfully but did NOT answer the question correctly:\n\n"
            f"```sql\n{prior_sql}\n```\n\n"
            f"Reason it was wrong:\n{verifier_reasoning}\n\n"
            f"Schema:\n{schema_text}\n\n"
            f"Original question: {question}\n\n"
            "Rewrite the SQL to correctly answer the question. Return ONLY the SQL."
        )
        step = "generator-regeneration"
    else:
        # Initial generation
        user_content = (
            f"Question: {question}\n\n"
            f"Schema (use ONLY these tables and columns):\n{schema_text}"
        )
        step = "generator-initial"

    raw = call_llm(
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_content},
        ],
        step=step,
        budget=budget,
        max_tokens=512,
        temperature=0.1,
    )

    return _clean_sql(raw)


# ── helpers ───────────────────────────────────────────────────────────────────

def _schema_to_text(schema: dict[str, list[str]]) -> str:
    return "\n".join(f"{table}({', '.join(cols)})" for table, cols in schema.items())


def _clean_sql(raw: str) -> str:
    """Strip markdown fences and leading/trailing whitespace."""
    raw = raw.strip()
    # Remove ```sql ... ``` or ``` ... ```
    raw = re.sub(r"^```(?:sql)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()
