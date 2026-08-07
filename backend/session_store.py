"""
backend/session_store.py — In-memory map from dataset_id to session state.

Replaces Streamlit's st.session_state. Every HTTP request carries a
dataset_id (= file content hash) that identifies which DB/schema/cache
collection to use.

Thread-safety: dict operations in CPython are protected by the GIL for
simple reads/writes. A threading.Lock is used for the mutable update path.
"""

from __future__ import annotations

import threading
from typing import TypedDict


class SessionEntry(TypedDict):
    db_path: str
    schema: dict[str, list[str]]
    dataset_hash: str
    golden_ready: bool


_store: dict[str, SessionEntry] = {}
_lock = threading.Lock()


def set_session(dataset_id: str, entry: SessionEntry) -> None:
    with _lock:
        _store[dataset_id] = entry


def get_session(dataset_id: str) -> SessionEntry | None:
    return _store.get(dataset_id)


def update_golden_ready(dataset_id: str, ready: bool = True) -> None:
    with _lock:
        if dataset_id in _store:
            _store[dataset_id]["golden_ready"] = ready


def all_sessions() -> dict[str, SessionEntry]:
    return dict(_store)
