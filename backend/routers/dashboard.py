"""
backend/routers/dashboard.py — List golden queries + doc-QA entries for a dataset.

GET /dashboard/{dataset_id}
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from backend.schemas import DashboardResponse
from backend import session_store
from pipeline import cache as golden_cache
from pipeline.embedding import call_embedding

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/{dataset_id}", response_model=DashboardResponse)
async def get_dashboard(dataset_id: str) -> DashboardResponse:
    """Return all cached entries (golden + doc_qa) for a dataset."""
    entry = session_store.get_session(dataset_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found.")

    # Pull all entries by doing a broad similarity search with a placeholder embedding
    # ChromaDB doesn't have a "list all" API, so we embed a generic phrase and get top-N
    embeddings = call_embedding(["show me all data"])
    entries: list[dict] = []

    if embeddings:
        try:
            import chromadb
            from pipeline.cache import _get_chroma, _collection_name
            col = _get_chroma().get_collection(_collection_name(dataset_id))
            results = col.query(
                query_embeddings=[embeddings[0]],
                n_results=min(col.count(), 200),
                include=["documents", "metadatas", "distances"],
            )
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                entries.append({
                    "question": doc,
                    "sql": meta.get("sql", ""),
                    "answer": meta.get("answer", ""),
                    "source_type": meta.get("source_type", "golden_query"),
                    "source_pdf": meta.get("source_pdf"),
                    "source_page": meta.get("source_page"),
                    "role": meta.get("role"),
                    "section": meta.get("section"),
                    "similarity": round(1.0 - dist, 3),
                })
        except Exception as exc:
            logger.warning("Dashboard ChromaDB query failed: %s", exc)

    return DashboardResponse(
        dataset_id=dataset_id,
        golden_ready=entry["golden_ready"],
        entries=entries,
    )
