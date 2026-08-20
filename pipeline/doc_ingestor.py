"""
pipeline/doc_ingestor.py — Parse .docx questions + batch-run full pipeline.

Usage:
    from pipeline.doc_ingestor import parse_questions, run_batch

parse_questions(docx_path) -> list[dict]
    Returns: [{question, role, section}]

await run_batch(questions, db_path, schema, dataset_hash,
                offset=0, chunk_size=10, budget_per_q=15)
    Returns: (successes, failures, next_offset, is_last_chunk)
    successes: [{question, sql, answer, role, section}]
    failures:  [{question, role, section, reason}]
"""

from __future__ import annotations

import asyncio
import logging

from docx import Document

from pipeline.llm import CallBudget

from pipeline.selector import select_tables_and_fields
from pipeline.generator import generate_sql
from pipeline.executor import execute_and_verify
from pipeline.responder import generate_response

logger = logging.getLogger(__name__)

# How many questions run concurrently. The real throttle against Groq's RPM
# lives in pipeline.llm's shared RateLimiter — this cap just bounds memory/
# connection usage and keeps logs readable. Tune independently of RPM.
_MAX_CONCURRENT_QUESTIONS = 5


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


async def run_batch(
    questions: list[dict],
    db_path: str,
    schema: dict,
    dataset_hash: str,
    budget_per_q: int = 15,
    offset: int = 0,
    chunk_size: int = 10,
    cancel_event: asyncio.Event | None = None,
    priority: str = "background",
) -> tuple[list[dict], list[dict], int, bool]:
    """
    Run a chunk of questions (questions[offset : offset+chunk_size]) through
    the full pipeline, concurrently (bounded by _MAX_CONCURRENT_QUESTIONS).

    Actual outbound-request pacing against Groq is enforced centrally by
    pipeline.llm's RateLimiter; raising concurrency here does not risk more
    429s.

    If `cancel_event` is set, in-flight questions finish but no new ones start.

    Returns:
        (successes, failures, next_offset, is_last_chunk)
        successes: [{question, sql, answer, role, section}]
        failures:  [{question, role, section, reason}]
        next_offset: offset to pass on the next call
        is_last_chunk: True when this chunk reaches the end of questions
    """
    chunk = questions[offset : offset + chunk_size]
    next_offset = offset + len(chunk)
    is_last_chunk = next_offset >= len(questions)

    total = len(chunk)
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_QUESTIONS)
    results: list[dict | None] = [None] * total

    async def _worker(idx: int, item: dict) -> None:
        q = item["question"]
        role = item["role"]
        section = item["section"]
        true_pos = idx + offset + 1  # 1-based position in the full question list

        if cancel_event is not None and cancel_event.is_set():
            results[idx] = {
                "kind": "failure",
                "question": q, "role": role, "section": section,
                "reason": "Job cancelled (server shutdown) before this question started.",
            }
            return

        async with semaphore:
            if cancel_event is not None and cancel_event.is_set():
                results[idx] = {
                    "kind": "failure",
                    "question": q, "role": role, "section": section,
                    "reason": "Job cancelled (server shutdown) before this question started.",
                }
                return

            logger.info("[batch %d/%d] Running: %s", true_pos, len(questions), q[:80])
            try:
                result_entry = await _run_single(q, db_path, schema, dataset_hash, budget_per_q, priority=priority)
                if result_entry is None:
                    results[idx] = {
                        "kind": "failure",
                        "question": q, "role": role, "section": section,
                        "reason": "Pipeline returned no result (triage/out-of-scope)",
                    }
                elif not result_entry.get("is_real_answer"):
                    results[idx] = {
                        "kind": "failure",
                        "question": q, "role": role, "section": section,
                        "reason": "Fallback/placeholder answer — not a real LLM answer",
                    }
                else:
                    results[idx] = {
                        "kind": "success",
                        "question": q,
                        "sql": result_entry["sql"],
                        "answer": result_entry["answer"],
                        "role": role,
                        "section": section,
                    }
            except Exception as exc:
                logger.warning("[batch %d/%d] Failed: %s — %s", true_pos, len(questions), q[:60], exc)
                results[idx] = {
                    "kind": "failure",
                    "question": q, "role": role, "section": section,
                    "reason": str(exc),
                }

    await asyncio.gather(*(_worker(i, item) for i, item in enumerate(chunk)))

    successes = [r for r in results if r and r["kind"] == "success"]
    failures = [r for r in results if r and r["kind"] == "failure"]
    for r in successes + failures:
        r.pop("kind", None)

    logger.info(
        "[batch] Chunk done. %d succeeded, %d failed (offset %d→%d, last=%s).",
        len(successes), len(failures), offset, next_offset, is_last_chunk,
    )
    return successes, failures, next_offset, is_last_chunk


# ── internals ─────────────────────────────────────────────────────────────────

async def _run_single(
    question: str,
    db_path: str,
    schema: dict,
    dataset_hash: str,
    budget_per_q: int,
    priority: str = "background",
) -> dict | None:
    """Run one question through Steps 1–5. Returns result dict or None."""
    budget = CallBudget(max_calls=budget_per_q)

    # Step 1/2: Triage + Table/field selection
    status, selected_schema = await select_tables_and_fields(question, schema, budget, priority=priority)
    if status != "ok":
        logger.info("[batch] Skipped (triage=%s): %s", status, question[:60])
        return None

    # Step 3: Initial SQL generation
    sql = await generate_sql(question, selected_schema, budget, priority=priority)

    # Step 4: Execution + verification
    # _gen_fn is a simple pass-through: execute_and_verify forwards
    # priority=priority as a kwarg, which lands in **kwargs and is passed
    # directly to generate_sql (which now accepts it as a keyword argument).
    async def _gen_fn(**kwargs):
        return await generate_sql(**kwargs)

    result = await execute_and_verify(
        question=question,
        initial_sql=sql,
        selected_schema=selected_schema,
        db_path=db_path,
        budget=budget,
        generate_sql_fn=_gen_fn,
        priority=priority,
    )

    if not result.success:
        raise RuntimeError(
            result.error or "Execution/verification failed after all retries"
        )

    # Step 5: Response generation
    nl_answer, _fig, is_real_answer = await generate_response(question, result, budget, priority=priority)

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
