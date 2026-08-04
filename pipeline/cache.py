"""
pipeline/cache.py — ChromaDB semantic cache for golden queries.

One persistent collection per dataset (keyed by file content hash).
Lookup uses cosine similarity threshold (default 0.88).
"""

from __future__ import annotations

import hashlib
import logging
import os

import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)

# Persistent ChromaDB directory inside project
_CHROMA_DIR = os.path.join(os.path.dirname(__file__), "..", "chroma_store")
_client: chromadb.ClientAPI | None = None

SIMILARITY_THRESHOLD = 0.88  # cosine similarity — tune empirically


def _get_chroma() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=os.path.abspath(_CHROMA_DIR))
    return _client


def _collection_name(file_hash: str) -> str:
    # ChromaDB collection names: 3-63 chars, alphanumeric + hyphens
    return f"golden-{file_hash[:20]}"


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# ── store ─────────────────────────────────────────────────────────────────────

def store_golden(
    dataset_hash: str,
    questions: list[str],
    sqls: list[str],
    answers: list[str],
    embeddings: list[list[float]],
) -> None:
    """
    Bulk-insert golden query triples into the collection for this dataset.
    Skips entries already stored (idempotent by question text as ID).
    """
    if not questions:
        return

    col = _get_chroma().get_or_create_collection(
        name=_collection_name(dataset_hash),
        metadata={"hnsw:space": "cosine"},
    )

    ids = [hashlib.md5(q.encode()).hexdigest() for q in questions]
    col.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=questions,
        metadatas=[{"sql": s, "answer": a} for s, a in zip(sqls, answers)],
    )
    logger.info("Stored %d golden queries for dataset %s", len(questions), dataset_hash[:8])


# ── lookup ────────────────────────────────────────────────────────────────────

def lookup(
    dataset_hash: str,
    question_embedding: list[float],
    threshold: float = SIMILARITY_THRESHOLD,
) -> dict | None:
    """
    Query the collection for the nearest golden query.

    Returns {"question": str, "sql": str, "answer": str, "similarity": float}
    or None if no match above threshold.
    """
    try:
        col = _get_chroma().get_collection(_collection_name(dataset_hash))
    except Exception:
        return None  # collection doesn't exist yet

    try:
        results = col.query(
            query_embeddings=[question_embedding],
            n_results=1,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        logger.warning("ChromaDB query failed: %s", exc)
        return None

    if not results["ids"] or not results["ids"][0]:
        return None

    # ChromaDB cosine distance: 0 = identical, 2 = opposite
    # similarity = 1 - distance
    distance = results["distances"][0][0]
    similarity = 1.0 - distance

    if similarity < threshold:
        logger.info("Cache miss (similarity=%.3f < threshold=%.2f)", similarity, threshold)
        return None

    meta = results["metadatas"][0][0]
    logger.info(
        "Cache hit (similarity=%.3f): %s",
        similarity,
        results["documents"][0][0][:60],
    )
    return {
        "question": results["documents"][0][0],
        "sql": meta["sql"],
        "answer": meta["answer"],
        "similarity": similarity,
    }


def clear_dataset(dataset_hash: str) -> None:
    """Remove a dataset's golden query collection (e.g., when file is re-uploaded)."""
    try:
        _get_chroma().delete_collection(_collection_name(dataset_hash))
        logger.info("Cleared golden query cache for dataset %s", dataset_hash[:8])
    except Exception:
        pass
