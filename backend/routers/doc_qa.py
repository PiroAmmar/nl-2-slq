"""
backend/routers/doc_qa.py — Doc-QA ingestion trigger + status polling.

POST /doc_qa/ingest       — trigger ingestion job as an asyncio Task
GET  /doc_qa/status/{id}  — poll job status

Ingestion runs as a native asyncio Task (not FastAPI BackgroundTasks) so we
keep a handle to it: on server shutdown we can cancel in-flight jobs
cleanly (the whole pipeline is async now, so cancellation raises inside an
`await` rather than killing a blocked OS thread mid-HTTP-request).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

from fastapi import APIRouter, HTTPException

from backend.schemas import DocQAIngestRequest, DocQAIngestResponse, DocQAStatusResponse
from backend import session_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/doc_qa", tags=["doc_qa"])

_INPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "input_docs")

# In-memory job store: job_id -> status dict (+ "_task" / "_cancel_event", stripped before response)
_jobs: dict[str, dict] = {}


def all_running_tasks() -> list[asyncio.Task]:
    """Used by main.py's shutdown hook to cancel/await in-flight jobs."""
    return [j["_task"] for j in _jobs.values() if j.get("_task") is not None and not j["_task"].done()]


def request_all_cancel() -> None:
    for job in _jobs.values():
        event: asyncio.Event | None = job.get("_cancel_event")
        if event is not None:
            event.set()


async def _run_ingest_job(job_id: str, docx_path: str, db_path: str, schema: dict, dataset_hash: str) -> None:
    """Async job body: run full ingestion, update job status."""
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


@router.post("/ingest", response_model=DocQAIngestResponse)
async def ingest_doc(req: DocQAIngestRequest) -> DocQAIngestResponse:
    """Trigger doc-QA ingestion for a .docx file in input_docs/."""
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
    _jobs[job_id] = {"status": "queued", "_cancel_event": cancel_event, "_task": None}

    task = asyncio.create_task(
        _run_ingest_job(job_id, docx_path, entry["db_path"], entry["schema"], entry["dataset_hash"])
    )
    _jobs[job_id]["_task"] = task

    return DocQAIngestResponse(job_id=job_id, message="Ingestion started. Poll /doc_qa/status/{job_id}.")


@router.get("/status/{job_id}", response_model=DocQAStatusResponse)
async def ingest_status(job_id: str) -> DocQAStatusResponse:
    """Poll the status of a doc-QA ingestion job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    public = {k: v for k, v in job.items() if not k.startswith("_")}
    return DocQAStatusResponse(job_id=job_id, **public)
