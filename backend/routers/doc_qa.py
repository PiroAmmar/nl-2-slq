"""
backend/routers/doc_qa.py — Doc-QA ingestion trigger + status polling.

POST /doc_qa/ingest          — trigger chunked ingestion (one chunk = batch_size questions)
GET  /doc_qa/status/{id}    — poll job status & chunk progress
POST /doc_qa/resume/{id}    — advance to next chunk after "awaiting_confirmation"
POST /doc_qa/cancel/{id}    — stop job (works in-flight or while paused)

Ingestion is split into discrete chunks.  Each chunk runs as one asyncio.Task
that completes and exits after the chunk finishes — the job persists state in
the in-memory _jobs dict.  Resuming spawns a brand-new Task.  This means:
  - A paused job holds NO asyncio.Task (all_running_tasks() naturally skips it).
  - Server restarts during a pause are safe — re-connect and poll /status.
  - The BACKGROUND_JOB_SEMAPHORE is released between chunks, never held while
    waiting for user input.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

from fastapi import APIRouter, HTTPException

from backend.schemas import (
    DocQAIngestRequest,
    DocQAIngestResponse,
    DocQAStatusResponse,
)
from backend import session_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/doc_qa", tags=["doc_qa"])

_INPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "input_docs")

# In-memory job store:
#   job_id -> {
#     "status": str,
#     "job_state": dict,          # mutated in place by ingest_chunk
#     "_cancel_event": Event,
#     "_task": Task | None,
#     "current_batch": int,
#     "batch_total": int | None,
#     "total_processed": int,
#     "pdf_path": str | None,
#     "success_count": int | None,   # final only
#     "failure_count": int | None,   # final only
#     "total": int | None,           # final only
#     "failures": list | None,       # final only
#     "error": str | None,
#   }
_jobs: dict[str, dict] = {}


def all_running_tasks() -> list[asyncio.Task]:
    """Used by main.py shutdown hook to cancel/await in-flight chunk tasks."""
    return [
        j["_task"]
        for j in _jobs.values()
        if j.get("_task") is not None and not j["_task"].done()
    ]


def request_all_cancel() -> None:
    """Signal every actively-running chunk to stop (shutdown path)."""
    for job in _jobs.values():
        event: asyncio.Event | None = job.get("_cancel_event")
        if event is not None:
            event.set()


# ── Chunk runner ──────────────────────────────────────────────────────────────

async def _run_chunk_job(job_id: str, chunk_size: int) -> None:
    """
    Run exactly ONE chunk of the ingestion job identified by job_id.

    On completion:
      - is_last_chunk=True  → status "done"  (job finished)
      - is_last_chunk=False → status "awaiting_confirmation" (user must resume)
    The function returns normally in both cases, completing the Task.
    The router's resume endpoint spawns a new Task for the next chunk.
    """
    from pipeline.doc_qa_runner import ingest_chunk

    job = _jobs[job_id]
    cancel_event: asyncio.Event = job["_cancel_event"]

    # Re-check: user may have cancelled while we were queued/waiting.
    if cancel_event.is_set():
        job["status"] = "cancelled"
        return

    job["status"] = "running"
    try:
        result = await ingest_chunk(
            job_state=job["job_state"],
            cancel_event=cancel_event,
            chunk_size=chunk_size,
        )
    except asyncio.CancelledError:
        logger.warning("[doc_qa job %s] Chunk cancelled (shutdown).", job_id)
        job["status"] = "cancelled"
        raise
    except Exception as exc:
        logger.error("[doc_qa job %s] Chunk failed: %s", job_id, exc)
        job.update({"status": "failed", "error": str(exc)})
        return

    # Update shared progress fields (visible via /status before final state).
    job["current_batch"] = result["current_batch"]
    job["batch_total"] = result["batch_total"]
    job["total_processed"] = result["total_processed"]
    job["pdf_path"] = result["pdf_path"]

    if result["is_last_chunk"] or cancel_event.is_set():
        final_status = "cancelled" if cancel_event.is_set() else "done"
        job.update({
            "status": final_status,
            "total": result["total"],
            "success_count": len(job["job_state"]["all_successes"]),
            "failure_count": len(job["job_state"]["all_failures"]),
            "failures": job["job_state"]["all_failures"],
        })
        logger.info(
            "[doc_qa job %s] %s. %d/%d succeeded.",
            job_id, final_status,
            job["success_count"], job["total"],
        )
    else:
        job["status"] = "awaiting_confirmation"
        logger.info(
            "[doc_qa job %s] Batch %d/%d done (%d processed). Awaiting resume.",
            job_id,
            result["current_batch"],
            result["batch_total"],
            result["total_processed"],
        )


# ── Legacy one-shot runner — now CLI/backward-compat only, NOT called by HTTP API ──

async def _run_ingest_job(
    job_id: str, docx_path: str, db_path: str, schema: dict, dataset_hash: str
) -> None:
    """
    DEPRECATED for HTTP use — kept only so CLI callers of pipeline/doc_qa_runner.py's
    `ingest()` continue to work. The HTTP /ingest route now calls _run_chunk_job instead.
    """
    from pipeline.doc_qa_runner import ingest

    cancel_event: asyncio.Event = _jobs[job_id]["_cancel_event"]
    _jobs[job_id]["status"] = "running"
    try:
        summary = await ingest(
            docx_path=docx_path,
            db_path=db_path,
            schema=schema,
            dataset_hash=dataset_hash,
            cancel_event=cancel_event,
        )
        _jobs[job_id].update({
            "status": "cancelled" if cancel_event.is_set() else "done",
            "total": summary["total"],
            "success_count": summary["success_count"],
            "failure_count": summary["failure_count"],
            "pdf_path": summary["pdf_path"],
            "failures": summary["failures"],
        })
    except asyncio.CancelledError:
        logger.warning("[doc_qa job %s] Cancelled (server shutdown).", job_id)
        _jobs[job_id]["status"] = "cancelled"
        raise
    except Exception as exc:
        logger.error("[doc_qa job %s] Failed: %s", job_id, exc)
        _jobs[job_id].update({"status": "failed", "error": str(exc)})


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/ingest", response_model=DocQAIngestResponse)
async def ingest_doc(req: DocQAIngestRequest) -> DocQAIngestResponse:
    """Trigger chunked doc-QA ingestion for a .docx file in input_docs/."""
    entry = session_store.get_session(req.dataset_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{req.dataset_id}' not found.")

    docx_path = os.path.join(_INPUT_DIR, req.docx_filename)
    if not os.path.exists(docx_path):
        raise HTTPException(
            status_code=404,
            detail=f"'{req.docx_filename}' not found in input_docs/. Upload it first.",
        )

    job_id = str(uuid.uuid4())
    cancel_event = asyncio.Event()

    _jobs[job_id] = {
        "status": "queued",
        # job_state is initialised on first call to ingest_chunk (first call
        # does parse_questions + offset=0 setup).
        "job_state": {
            "docx_path": docx_path,
            "db_path": entry["db_path"],
            "schema": entry["schema"],
            "dataset_hash": entry["dataset_hash"],
        },
        "_cancel_event": cancel_event,
        "_task": None,
        "current_batch": 0,
        "batch_total": None,
        "total_processed": 0,
        "pdf_path": None,
        "success_count": None,
        "failure_count": None,
        "total": None,
        "failures": None,
        "error": None,
    }

    task = asyncio.create_task(
        _run_chunk_job(job_id, chunk_size=req.batch_size)
    )
    _jobs[job_id]["_task"] = task

    return DocQAIngestResponse(
        job_id=job_id,
        message=f"Chunked ingestion started (batch_size={req.batch_size}). Poll /doc_qa/status/{job_id}.",
    )


@router.post("/resume/{job_id}", response_model=DocQAIngestResponse)
async def resume_ingest(job_id: str, batch_size: int = 10) -> DocQAIngestResponse:
    """
    Advance to the next chunk after job reached 'awaiting_confirmation'.
    Spawns a new asyncio.Task for the next chunk; returns immediately.
    """
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    if job["status"] != "awaiting_confirmation":
        raise HTTPException(
            status_code=400,
            detail=f"Job is '{job['status']}', not 'awaiting_confirmation'. Cannot resume.",
        )

    job["status"] = "running"
    task = asyncio.create_task(_run_chunk_job(job_id, chunk_size=batch_size))
    job["_task"] = task

    return DocQAIngestResponse(
        job_id=job_id,
        message=f"Resumed batch {job['current_batch'] + 1}/{job['batch_total'] or '?'}.",
    )


@router.post("/cancel/{job_id}", response_model=DocQAIngestResponse)
async def cancel_ingest(job_id: str) -> DocQAIngestResponse:
    """
    Stop a job.
    - If currently running (active chunk task): sets cancel_event so run_batch
      exits cleanly after the in-flight question(s) finish.
    - If awaiting_confirmation (no active task): sets status to 'cancelled'
      directly — partial results from completed chunks remain on disk/in ChromaDB.
    """
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if job["status"] == "running":
        job["_cancel_event"].set()
        msg = "Cancellation requested — in-flight chunk will finish then stop."
    elif job["status"] == "awaiting_confirmation":
        job["status"] = "cancelled"
        msg = "Job cancelled. Partial results from completed batches are preserved."
    elif job["status"] in ("done", "cancelled", "failed"):
        msg = f"Job already in terminal state '{job['status']}' — no action taken."
    else:
        job["status"] = "cancelled"
        msg = "Job cancelled."

    return DocQAIngestResponse(job_id=job_id, message=msg)


@router.get("/status/{job_id}", response_model=DocQAStatusResponse)
async def ingest_status(job_id: str) -> DocQAStatusResponse:
    """Poll the status of a doc-QA ingestion job (full + chunk progress)."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    # Strip private/internal keys before constructing the response.
    return DocQAStatusResponse(
        job_id=job_id,
        status=job["status"],
        total=job.get("total"),
        success_count=job.get("success_count"),
        failure_count=job.get("failure_count"),
        current_batch=job.get("current_batch") or None,
        batch_total=job.get("batch_total"),
        total_processed=job.get("total_processed") or None,
        pdf_path=job.get("pdf_path"),
        failures=job.get("failures"),
        error=job.get("error"),
    )
