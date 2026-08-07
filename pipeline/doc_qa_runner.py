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

logger = logging.getLogger(__name__)

_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output_docs")
_INPUT_DIR  = os.path.join(os.path.dirname(__file__), "..", "input_docs")
_BUDGET_PER_Q = 15   # higher than interactive (12) — batch is not latency-sensitive


def ingest(
    docx_path: str,
    db_path: str,
    schema: dict,
    dataset_hash: str,
) -> dict:
    """
    Full ingestion pipeline:
      1. Parse questions from .docx
      2. Batch-run pipeline per question
      3. Write PDF (exact page numbers via fpdf2)
      4. Embed successes + store in ChromaDB as doc_qa entries
      5. Return run summary

    Returns:
        {total, success_count, failure_count, pdf_path, failures}
    """
    os.makedirs(_OUTPUT_DIR, exist_ok=True)
    os.makedirs(_INPUT_DIR, exist_ok=True)   # create if missing — no manual setup needed

    # ── Step 1: Parse ─────────────────────────────────────────────────────────
    logger.info("[doc_qa] Parsing questions from %s", docx_path)
    questions = parse_questions(docx_path)
    if not questions:
        logger.warning("[doc_qa] No questions found in %s", docx_path)
        return {"total": 0, "success_count": 0, "failure_count": 0, "pdf_path": None, "failures": []}


    # ── Step 2: Batch run pipeline ────────────────────────────────────────────
    logger.info("[doc_qa] Running %d questions through pipeline", len(questions))
    successes, failures = run_batch(
        questions=questions,
        db_path=db_path,
        schema=schema,
        dataset_hash=dataset_hash,
        budget_per_q=_BUDGET_PER_Q,
    )

    # ── Step 3: PDF generation ────────────────────────────────────────────────
    pdf_path = None
    page_meta: list[dict] = []

    if successes:
        doc_stem = os.path.splitext(os.path.basename(docx_path))[0]
        # Deterministic filename keyed by content + dataset
        doc_hash = hashlib.sha256(doc_stem.encode()).hexdigest()[:8]
        pdf_filename = f"{doc_stem}_{dataset_hash[:8]}_{doc_hash}.pdf"
        pdf_path = os.path.join(_OUTPUT_DIR, pdf_filename)

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
    summary = ingest(
        docx_path=args.docx,
        db_path=args.db,
        schema=schema,
        dataset_hash=args.dataset_hash,
    )
    print(json.dumps(summary, indent=2, default=str))
