"""
backend/deps.py — Shared FastAPI dependency injection.

Provides:
- get_session() — resolve dataset_id → SessionEntry or 404
- CallBudget factory
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from pipeline.llm import CallBudget
from backend.session_store import get_session, SessionEntry

_BUDGET_MAX_CALLS = 12   # same as interactive chat


def get_session_entry(dataset_id: str = Query(..., description="Dataset ID from /datasets/upload")) -> SessionEntry:
    """Resolve dataset_id to a session entry, or raise 404."""
    entry = get_session(dataset_id)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Dataset '{dataset_id}' not found. Upload a file first via POST /datasets/upload.",
        )
    return entry


def make_budget(max_calls: int = _BUDGET_MAX_CALLS) -> CallBudget:
    return CallBudget(max_calls=max_calls)
