"""
pipeline/doc_qa_runner.py — Orchestrator for doc-QA ingestion.

Callable as:
    python -m pipeline.doc_qa_runner \\
        --docx input_docs/Demo\\ Objectives\\ Questions.docx \\
        --db   /path/to/data.db \\
        --hash <dataset_hash>

Also importable by FastAPI BackgroundTasks:
    from pipeline.doc_qa_runner import ingest
    background_tasks.add_task(ingest, docx_path, db_path, schema, dataset_hash)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sqlite3

from dotenv import load_dotenv

load_dotenv()

from pipeline.doc_ingestor import parse_questions, run_batch
from pipeline.pdf_writer import write_qa_pdf
from pipeline.embedding import call_embedding
from pipeline import cache as golden_cache
from pipeline.llm import BACKGROUND_JOB_SEMAPHORE

logger = logging.getLogger(__name__)

_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output_docs")
_INPUT_DIR  = os.path.join(os.path.dirname(__file__), "..", "input_docs")
_BUDGET_PER_Q = 15   # higher than interactive (12) — batch is not latency-sensitive


def _make_pdf_path(docx_path: str, dataset_hash: str) -> str:
    """Derive a deterministic PDF output path from the docx filename + dataset hash."""
    doc_stem = os.path.splitext(os.path.basename(docx_path))[0]
    doc_hash = hashlib.sha256(doc_stem.encode()).hexdigest()[:8]
    pdf_filename = f"{doc_stem}_{dataset_hash[:8]}_{doc_hash}.pdf"
    return os.path.join(_OUTPUT_DIR, pdf_filename)


async def ingest(
    docx_path: str,
    db_path: str,
    schema: dict,
    dataset_hash: str,
    cancel_event: asyncio.Event | None = None,
) -> dict:
    """
    Full ingestion pipeline:
      1. Parse questions from .docx
      2. Batch-run pipeline per question (bounded concurrency, shared rate limit)
      3. Write PDF (exact page numbers via fpdf2)
      4. Embed successes + store in ChromaDB as doc_qa entries
      5. Return run summary

    `cancel_event`, if provided, lets a caller (e.g. server shutdown) stop
    new questions from starting without killing in-flight ones mid-request.

    Returns:
        {total, success_count, failure_count, pdf_path, failures}
    """
    os.makedirs(_OUTPUT_DIR, exist_ok=True)
    os.makedirs(_INPUT_DIR, exist_ok=True)   # create if missing — no manual setup needed

    # Acquire the process-wide background-job semaphore. This ensures only
    # GOLDEN_GEN_MAX_CONCURRENT (default: 1) doc-ingest or golden-gen jobs
    # run at a time, preventing uncontrolled concurrency against Groq's RPM.
    # The semaphore is released as soon as ingest() exits (normally or on error).
    async with BACKGROUND_JOB_SEMAPHORE:
        # Re-check cancellation immediately after acquiring — the server may have
        # shut down while we were waiting for the semaphore.
        if cancel_event is not None and cancel_event.is_set():
            logger.warning("[doc_qa] Job cancelled while waiting for semaphore — aborting.")
            return {"total": 0, "success_count": 0, "failure_count": 0, "pdf_path": None, "failures": []}

        # ── Step 1: Parse ─────────────────────────────────────────────────────────
        logger.info("[doc_qa] Parsing questions from %s", docx_path)
        questions = parse_questions(docx_path)
        if not questions:
            logger.warning("[doc_qa] No questions found in %s", docx_path)
            return {"total": 0, "success_count": 0, "failure_count": 0, "pdf_path": None, "failures": []}


        # ── Step 2: Batch run pipeline ────────────────────────────────────────────
        logger.info("[doc_qa] Running %d questions through pipeline", len(questions))
        successes, failures, _next, _last = await run_batch(
            questions=questions,
            db_path=db_path,
            schema=schema,
            dataset_hash=dataset_hash,
            budget_per_q=_BUDGET_PER_Q,
            offset=0,
            chunk_size=len(questions),  # single chunk — full ingest, unchanged behaviour
            cancel_event=cancel_event,
            priority="background",
        )

        # ── Step 3: PDF generation ────────────────────────────────────────────────
        pdf_path = None
        page_meta: list[dict] = []

        if successes:
            pdf_path = _make_pdf_path(docx_path, dataset_hash)
            os.makedirs(_OUTPUT_DIR, exist_ok=True)

            logger.info("[doc_qa] Writing PDF to %s", pdf_path)
            page_meta = write_qa_pdf(successes, pdf_path)
        else:
            logger.warning("[doc_qa] No successful answers — skipping PDF generation")

        # ── Step 4: Embed + store in ChromaDB ────────────────────────────────────
        if successes and page_meta:
            questions_text = [s["question"] for s in successes]
            sqls = [s["sql"] for s in successes]
            answers = [s["answer"] for s in successes]

            logger.info("[doc_qa] Embedding %d successful answers", len(questions_text))
            embeddings = call_embedding(questions_text)

            if embeddings is None:
                logger.error("[doc_qa] Embedding failed — doc_qa entries NOT stored in cache")
            else:
                golden_cache.store_doc_qa(
                    dataset_hash=dataset_hash,
                    questions=questions_text,
                    sqls=sqls,
                    answers=answers,
                    embeddings=embeddings,
                    page_meta=page_meta,
                )
                logger.info("[doc_qa] Stored %d doc_qa entries in ChromaDB", len(questions_text))

        # ── Step 5: Summary ───────────────────────────────────────────────────────
        summary = {
            "total": len(questions),
            "success_count": len(successes),
            "failure_count": len(failures),
            "pdf_path": pdf_path,
            "failures": failures,
        }
        _log_summary(summary)
        return summary


# ── Chunked re-entrant helpers — used by doc_qa.py's _run_chunk_job —————————

async def _embed_and_store(
    successes: list[dict],
    page_meta: list[dict],
    dataset_hash: str,
) -> None:
    """
    Embed a list of successes and store them in ChromaDB as doc_qa entries.
    Factored out of ingest() so ingest_chunk() can call it per-chunk without
    re-embedding entries from prior chunks.
    """
    questions_text = [s["question"] for s in successes]
    sqls = [s["sql"] for s in successes]
    answers = [s["answer"] for s in successes]

    logger.info("[doc_qa] Embedding %d successful answers", len(questions_text))
    embeddings = call_embedding(questions_text)

    if embeddings is None:
        logger.error("[doc_qa] Embedding failed — doc_qa entries NOT stored in cache")
        return

    golden_cache.store_doc_qa(
        dataset_hash=dataset_hash,
        questions=questions_text,
        sqls=sqls,
        answers=answers,
        embeddings=embeddings,
        page_meta=page_meta,
    )
    logger.info("[doc_qa] Stored %d doc_qa entries in ChromaDB", len(questions_text))


async def ingest_chunk(
    job_state: dict,
    cancel_event: asyncio.Event,
    chunk_size: int = 10,
) -> dict:
    """
    Re-entrant chunked ingestion step. Intended to be called once per task.
    After each call the asyncio.Task exits; the router re-spawns a new task
    for every resume (task-exit-and-respawn design — see chunked_ingestion_plan_v3.md).

    job_state is mutated in-place and persisted in _jobs[job_id] by the router.
    First call initialises it; subsequent calls continue from the stored offset.

    Returns summary dict with keys:
        processed_this_chunk, total_processed, total, is_last_chunk,
        pdf_path, current_batch, batch_total
    """
    # First call: parse the docx and initialise running state.
    if "questions" not in job_state:
        os.makedirs(_OUTPUT_DIR, exist_ok=True)
        os.makedirs(_INPUT_DIR, exist_ok=True)
        job_state["questions"] = parse_questions(job_state["docx_path"])
        job_state["offset"] = 0
        job_state["all_successes"] = []
        job_state["all_failures"] = []
        job_state["pdf_path"] = _make_pdf_path(job_state["docx_path"], job_state["dataset_hash"])

    successes, failures, next_offset, is_last_chunk = await run_batch(
        job_state["questions"],
        job_state["db_path"],
        job_state["schema"],
        job_state["dataset_hash"],
        budget_per_q=_BUDGET_PER_Q,
        offset=job_state["offset"],
        chunk_size=chunk_size,
        cancel_event=cancel_event,
        priority="background",
    )

    job_state["all_successes"].extend(successes)
    job_state["all_failures"].extend(failures)

    page_meta: list[dict] = []
    if successes:
        # Full re-render of PDF with all accumulated successes so far.
        # write_qa_pdf uses atomic tmp+replace, so concurrent readers are safe.
        page_meta = write_qa_pdf(job_state["all_successes"], job_state["pdf_path"])
        # page_meta covers ALL accumulated successes; slice to just this chunk
        # before embedding so we don't re-embed already-stored questions.
        new_page_meta = page_meta[-len(successes):]
        await _embed_and_store(successes, new_page_meta, job_state["dataset_hash"])

    job_state["offset"] = next_offset

    total_q = len(job_state["questions"])
    batch_total = max(1, -(-total_q // chunk_size))  # ceiling division
    current_batch = max(1, -(-next_offset // chunk_size))

    return {
        "processed_this_chunk": len(successes) + len(failures),
        "total_processed": len(job_state["all_successes"]) + len(job_state["all_failures"]),
        "total": total_q,
        "is_last_chunk": is_last_chunk,
        "pdf_path": job_state["pdf_path"],
        "current_batch": current_batch,
        "batch_total": batch_total,
    }


def _log_summary(summary: dict) -> None:
    logger.info(
        "[doc_qa] Run complete: %d/%d succeeded, %d failed. PDF: %s",
        summary["success_count"],
        summary["total"],
        summary["failure_count"],
        summary["pdf_path"] or "none",
    )
    if summary["failures"]:
        logger.warning("[doc_qa] Failed questions:")
        for f in summary["failures"]:
            logger.warning("  [%s / %s] %s — %s", f.get("role"), f.get("section"), f.get("question", "")[:80], f.get("reason"))


def _schema_from_db(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    schema = {}
    for table in tables:
        cursor.execute(f"PRAGMA table_info('{table}');")
        schema[table] = [row[1] for row in cursor.fetchall()]
    conn.close()
    return schema


# ── CLI entrypoint ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Doc-QA ingestion runner")
    parser.add_argument("--docx", required=True, help="Path to .docx input file")
    parser.add_argument("--db", required=True, help="Path to SQLite .db file")
    parser.add_argument("--hash", required=True, dest="dataset_hash", help="Dataset hash (from cache.file_hash)")
    args = parser.parse_args()

    schema = _schema_from_db(args.db)
    summary = asyncio.run(ingest(
        docx_path=args.docx,
        db_path=args.db,
        schema=schema,
        dataset_hash=args.dataset_hash,
    ))
    print(json.dumps(summary, indent=2, default=str))
