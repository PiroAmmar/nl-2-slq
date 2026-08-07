"""
pipeline/triage.py — Step 3.1: classify incoming user query.

Uses Groq (cheap call, low token count).
Returns: "general" | "data" | "out_of_scope"
"""

from __future__ import annotations

import json
import logging

from pipeline.llm import CallBudget, call_llm

logger = logging.getLogger(__name__)

_SYSTEM = """You are a query classifier for a Natural-Language-to-SQL assistant.
Classify the user's question into exactly one of three categories:
- "data": the question asks for information answerable by querying a database/dataset
- "general": a general conversational or common-knowledge question
- "out_of_scope": harmful, irrelevant, or cannot be answered from the dataset or general knowledge

Reply with ONLY valid JSON, no markdown fences:
{"category": "data" | "general" | "out_of_scope", "reason": "<one short sentence>"}"""


async def classify_query(question: str, budget: CallBudget) -> str:
    """
    Returns "data", "general", or "out_of_scope".
    Falls back to "data" on parse error to keep pipeline unblocked.
    """
    raw = await call_llm(
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": question},
        ],
        step="triage",
        budget=budget,
        max_tokens=80,
        temperature=0.0,
    )

    try:
        parsed = json.loads(raw)
        category = parsed.get("category", "data")
        if category not in ("data", "general", "out_of_scope"):
            category = "data"
        logger.info("Triage → %s | %s", category, parsed.get("reason", ""))
        return category
    except json.JSONDecodeError:
        logger.warning("Triage JSON parse failed, defaulting to 'data'. Raw: %s", raw[:120])
        return "data"
