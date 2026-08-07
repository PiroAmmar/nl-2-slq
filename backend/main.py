"""
backend/main.py — FastAPI application entry point.

Run with:
    uvicorn backend.main:app --reload --port 8000

Mounts:
    /output_docs  → StaticFiles (PDFs for deep-link)
    /api/...      → Routers
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from backend.routers import datasets, query, dashboard, doc_qa

_SHUTDOWN_GRACE_S = 5.0  # let in-flight questions wrap up before hard-cancelling


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    # ── graceful shutdown: stop doc_qa jobs cleanly instead of dying mid-sleep ──
    doc_qa.request_all_cancel()
    tasks = doc_qa.all_running_tasks()
    if not tasks:
        return
    logger.info("Shutdown: waiting up to %.0fs for %d doc_qa job(s) to wind down.", _SHUTDOWN_GRACE_S, len(tasks))
    _done, pending = await asyncio.wait(tasks, timeout=_SHUTDOWN_GRACE_S)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


logger = logging.getLogger(__name__)

app = FastAPI(
    title="NL-to-SQL API",
    description="Natural-language-to-SQL RAG pipeline — FastAPI backend.",
    version="2.0.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],  # Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files: PDF deep-links ──────────────────────────────────────────────
_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output_docs")
os.makedirs(_OUTPUT_DIR, exist_ok=True)
app.mount("/output_docs", StaticFiles(directory=_OUTPUT_DIR), name="output_docs")

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(datasets.router)
app.include_router(query.router)
app.include_router(dashboard.router)
app.include_router(doc_qa.router)


# ── Global error handler — never expose raw internals (api-patterns rule) ─────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logging.getLogger(__name__).error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again."},
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
