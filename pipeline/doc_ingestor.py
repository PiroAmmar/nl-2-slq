"""
pipeline/doc_ingestor.py — Parse .docx questions + batch-run full pipeline.

Usage:
    from pipeline.doc_ingestor import parse_questions, run_batch

parse_questions(docx_path) -> list[dict]
    Returns: [{question, role, section}]

run_batch(questions, db_path, schema, dataset_hash, budget_per_q=15)
    Returns: (successes, failures)
    successes: [{question, sql, answer, role, section}]
    failures:  [{question, role, section, reason}]
"""

from __future__ import annotations

import logging
import time
import random

from docx import Document

from pipeline.llm import CallBudget

from pipeline.selector import select_tables_and_fields
from pipeline.generator import generate_sql
from pipeline.executor import execute_and_verify
from pipeline.responder import generate_response

logger = logging.getLogger(__name__)

# Seconds to wait between questions — respects Groq rate limits (§7 Prompt.md)
_INTER_QUESTION_DELAY_S = 2.0
_JITTER_S = 0.5


def parse_questions(docx_path: str) -> list[dict]:
    """
    Parse a .docx file and extract numbered questions with their role/section context.

    Walks paragraphs in order. Heading-style paragraphs set the current role/section
    context; numbered list items (or paragraphs that look like "1. Question...") are
    extracted as questions.

    Returns list of dicts: {question: str, role: str, section: str}
    """
    doc = Document(docx_path)
    questions: list[dict] = []
    current_role = ""
    current_section = ""

    import re
    found_first_role = False

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        style_name = para.style.name if para.style else ""

        # Heading 1 → role, Heading 2/3 → section
        if "Heading 1" in style_name:
            current_role = text
            current_section = ""
            found_first_role = True
            continue
        if "Heading 2" in style_name or "Heading 3" in style_name:
            current_section = text
            continue

        # Check for role pattern e.g., "1. Sales Director (30 Questions)"
        role_match = re.match(r"^\d+\.\s+(.*?)\s*(?:\(\d+\s+Questions?\))?$", text, flags=re.IGNORECASE)
        if role_match:
            current_role = role_match.group(1).strip()
            current_section = ""
            found_first_role = True
            continue

        if not found_first_role:
            continue

        ends_with_punct = text.endswith("?") or text.endswith(".")
        is_list_item = style_name.startswith("List")
        is_numbered = _is_numbered_question(text)

        is_sentence = ends_with_punct or is_numbered or is_list_item or len(text.split()) > 5

        if not is_sentence and len(text.split()) <= 4:
            current_section = text
            continue

        question_text = _strip_number_prefix(text)
        if question_text:
            questions.append(
                {
                    "question": question_text,
                    "role": current_role,
                    "section": current_section,
                }
            )

    logger.info(
        "Parsed %d questions from %s (roles/sections tracked from headings)",
        len(questions),
        docx_path,
    )
    return questions


def run_batch(
    questions: list[dict],
    db_path: str,
    schema: dict,
    dataset_hash: str,
    budget_per_q: int = 15,
) -> tuple[list[dict], list[dict]]:
    """
    Run every question through the full pipeline (Steps 1-5).

    Rate-limited: waits _INTER_QUESTION_DELAY_S + jitter between questions.
    Failed questions (never passed verification, budget exhausted, etc.) are
    collected in `failures` — never silently dropped.

    Returns:
        successes: [{question, sql, answer, role, section, is_real_answer}]
        failures:  [{question, role, section, reason}]
    """
    successes: list[dict] = []
    failures: list[dict] = []
    total = len(questions)

    for idx, item in enumerate(questions):
        q = item["question"]
        role = item["role"]
        section = item["section"]
        logger.info("[batch %d/%d] Running: %s", idx + 1, total, q[:80])

        try:
            result_entry = _run_single(q, db_path, schema, dataset_hash, budget_per_q)
            if result_entry is None:
                failures.append(
                    {
                        "question": q,
                        "role": role,
                        "section": section,
                        "reason": "Pipeline returned no result (triage/out-of-scope)",
                    }
                )
            elif not result_entry.get("is_real_answer"):
                failures.append(
                    {
                        "question": q,
                        "role": role,
                        "section": section,
                        "reason": "Fallback/placeholder answer — not a real LLM answer",
                    }
                )
            else:
                successes.append(
                    {
                        "question": q,
                        "sql": result_entry["sql"],
                        "answer": result_entry["answer"],
                        "role": role,
                        "section": section,
                    }
                )
        except Exception as exc:
            logger.warning("[batch %d/%d] Failed: %s — %s", idx + 1, total, q[:60], exc)
            failures.append(
                {
                    "question": q,
                    "role": role,
                    "section": section,
                    "reason": str(exc),
                }
            )

        # Rate-limit pause between questions (skip after last question)
        if idx < total - 1:
            wait = _INTER_QUESTION_DELAY_S + random.uniform(0, _JITTER_S)
            time.sleep(wait)

    logger.info(
        "[batch] Done. %d succeeded, %d failed out of %d total.",
        len(successes),
        len(failures),
        total,
    )
    return successes, failures


# ── internals ─────────────────────────────────────────────────────────────────

def _run_single(
    question: str,
    db_path: str,
    schema: dict,
    dataset_hash: str,
    budget_per_q: int,
) -> dict | None:
    """Run one question through Steps 1–5. Returns result dict or None."""
    budget = CallBudget(max_calls=budget_per_q)

    # Step 1/2: Triage + Table/field selection
    status, selected_schema = select_tables_and_fields(question, schema, budget)
    if status != "ok":
        logger.info("[batch] Skipped (triage=%s): %s", status, question[:60])
        return None

    # Step 3: Initial SQL generation
    sql = generate_sql(question, selected_schema, budget)

    # Step 4: Execution + verification
    def _gen_fn(**kwargs):
        return generate_sql(**kwargs)

    result = execute_and_verify(
        question=question,
        initial_sql=sql,
        selected_schema=selected_schema,
        db_path=db_path,
        budget=budget,
        generate_sql_fn=_gen_fn,
    )

    if not result.success:
        raise RuntimeError(
            result.error or "Execution/verification failed after all retries"
        )

    # Step 5: Response generation
    nl_answer, _fig, is_real_answer = generate_response(question, result, budget)

    return {
        "sql": result.sql,
        "answer": nl_answer,
        "is_real_answer": is_real_answer,
    }


def _is_numbered_question(text: str) -> bool:
    """Return True if text looks like '42. Some question...' or '42) ...'"""
    import re
    return bool(re.match(r"^\d+[.)]\s+\S", text))


def _strip_number_prefix(text: str) -> str:
    """Remove leading number prefix like '42. ' or '42) '"""
    import re
    return re.sub(r"^\d+[.)]\s+", "", text).strip()
