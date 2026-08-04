"""
pipeline/selector.py — Step 3.2: table and field selection.

Uses Groq. Two-pass for large schemas (tables first, then fields).
Returns: dict[table_name, list[field_name]]
"""

from __future__ import annotations

import json
import logging

from pipeline.llm import CallBudget, call_llm

logger = logging.getLogger(__name__)

_TABLE_SYSTEM = """You are a database expert.
Given a user question and a list of table names, select ONLY the tables needed.
Reply with ONLY valid JSON (no markdown):
{"tables": ["table1", "table2"]}"""

_FIELD_SYSTEM = """You are a database expert.
Given a user question and a database schema, return ONLY the columns needed to answer the question.
Reply with ONLY valid JSON (no markdown):
{"fields": {"table_name": ["col1", "col2"]}}"""

_TWO_PASS_THRESHOLD = 2000  # schema char count above which we split into two calls


def select_tables_and_fields(
    question: str,
    schema: dict[str, list[str]],
    budget: CallBudget,
) -> dict[str, list[str]]:
    """
    schema: {table_name: [col1, col2, ...]}
    Returns a filtered subset. Falls back to full schema on parse error.
    """
    schema_text = _schema_to_text(schema)
    if len(schema_text) > _TWO_PASS_THRESHOLD:
        return _two_pass(question, schema, budget)
    return _one_pass(question, schema, schema_text, budget)


def _one_pass(
    question: str,
    schema: dict[str, list[str]],
    schema_text: str,
    budget: CallBudget,
) -> dict[str, list[str]]:
    raw = call_llm(
        messages=[
            {"role": "system", "content": _FIELD_SYSTEM},
            {
                "role": "user",
                "content": f"Question: {question}\n\nSchema:\n{schema_text}",
            },
        ],
        step="selector-one-pass",
        budget=budget,
        max_tokens=512,
        temperature=0.0,
    )
    return _parse_fields(raw, schema)


def _two_pass(
    question: str,
    schema: dict[str, list[str]],
    budget: CallBudget,
) -> dict[str, list[str]]:
    # Pass 1: table names only
    table_list = "\n".join(f"- {t}" for t in schema)
    raw1 = call_llm(
        messages=[
            {"role": "system", "content": _TABLE_SYSTEM},
            {
                "role": "user",
                "content": f"Question: {question}\n\nAvailable tables:\n{table_list}",
            },
        ],
        step="selector-tables",
        budget=budget,
        max_tokens=256,
        temperature=0.0,
    )
    try:
        selected = [t for t in json.loads(raw1).get("tables", []) if t in schema]
    except json.JSONDecodeError:
        selected = list(schema.keys())

    if not selected:
        selected = list(schema.keys())

    # Pass 2: fields on pruned schema
    pruned = {t: schema[t] for t in selected}
    raw2 = call_llm(
        messages=[
            {"role": "system", "content": _FIELD_SYSTEM},
            {
                "role": "user",
                "content": f"Question: {question}\n\nSchema:\n{_schema_to_text(pruned)}",
            },
        ],
        step="selector-fields",
        budget=budget,
        max_tokens=512,
        temperature=0.0,
    )
    return _parse_fields(raw2, pruned)


def _schema_to_text(schema: dict[str, list[str]]) -> str:
    return "\n".join(f"{t}: {', '.join(cols)}" for t, cols in schema.items())


def _parse_fields(raw: str, fallback: dict[str, list[str]]) -> dict[str, list[str]]:
    try:
        fields: dict = json.loads(raw).get("fields", {})
        result = {
            t: [c for c in cols if c in fallback.get(t, cols)]
            for t, cols in fields.items()
            if t in fallback
        }
        return result if result else fallback
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Selector parse failed, using full schema.")
        return fallback
