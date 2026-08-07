"""
backend/routers/doc_qa.py — Doc-QA ingestion trigger + status polling.

POST /doc_qa/ingest       — trigger ingestion job as BackgroundTask
GET  /doc_qa/status/{id}  — poll job status
"""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException

from backend.schemas import DocQAIngestRequest, DocQAIngestResponse, DocQAStatusResponse
from backend import session_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/doc_qa", tags=["doc_qa"])

_INPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "input_docs")

# In-memory job store: job_id -> status dict
_jobs: dict[str, dict] = {}


def _run_ingest_job(job_id: str, docx_path: str, db_path: str, schema: dict, dataset_hash: str) -> None:
    """Background task: run full ingestion, update job status."""
    from pipeline.doc_qa_runner import ingest
    _jobs[job_id]["status"] = "running"
    try:
        summary = ingest(
            docx_path=docx_path,
            db_path=db_path,
            schema=schema,
            dataset_hash=dataset_hash,
        )
        _jobs[job_id].update({
            "status": "done",
            "total": summary["total"],
            "success_count": summary["success_count"],
            "failure_count": summary["failure_count"],
            "pdf_path": summary["pdf_path"],
            "failures": summary["failures"],
        })
    except Exception as exc:
        logger.error("[doc_qa job %s] Failed: %s", job_id, exc)
        _jobs[job_id].update({"status": "failed", "error": str(exc)})


@router.post("/ingest", response_model=DocQAIngestResponse)
async def ingest_doc(req: DocQAIngestRequest, background_tasks: BackgroundTasks) -> DocQAIngestResponse:
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
    _jobs[job_id] = {"status": "queued"}

    background_tasks.add_task(
        _run_ingest_job,
        job_id,
        docx_path,
        entry["db_path"],
        entry["schema"],
        entry["dataset_hash"],
    )

    return DocQAIngestResponse(job_id=job_id, message="Ingestion started. Poll /doc_qa/status/{job_id}.")


@router.get("/status/{job_id}", response_model=DocQAStatusResponse)
async def ingest_status(job_id: str) -> DocQAStatusResponse:
    """Poll the status of a doc-QA ingestion job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return DocQAStatusResponse(job_id=job_id, **job)
