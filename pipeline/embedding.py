"""
pipeline/embedding.py — gemini-embedding-001 wrapper with batching + retry.

On repeated failure, returns None so the caller can skip cache and fall
through to the standard pipeline (non-blocking degradation).
"""

from __future__ import annotations

import logging
import os
import time
import random

from google import genai

logger = logging.getLogger(__name__)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    return _client


def call_embedding(
    texts: list[str],
    max_retries: int = 3,
    model: str = "gemini-embedding-001",
) -> list[list[float]] | None:
    """
    Embed a batch of texts. Returns list of float vectors, or None on failure.

    Batches all texts in a single API call when possible.
    On 429/5xx, retries with exponential backoff + jitter.
    On permanent failure, returns None (caller must handle gracefully).
    """
    if not texts:
        return []

    client = _get_client()
    attempt = 0
    base_wait = 1.0
    last_error: Exception | None = None

    while attempt <= max_retries:
        try:
            result = client.models.embed_content(
                model=model,
                contents=texts,
            )
            # result.embeddings is a list of ContentEmbedding objects
            vectors = [e.values for e in result.embeddings]
            logger.info("Embedded %d texts OK", len(texts))
            return vectors

        except Exception as exc:
            last_error = exc
            err_str = str(exc)
            is_transient = any(
                tok in err_str for tok in ("429", "500", "503", "quota", "rate")
            )
            if not is_transient:
                logger.error("Embedding non-retryable error: %s", exc)
                return None

            wait = base_wait * (2 ** attempt) + random.uniform(0, 0.5)
            logger.warning(
                "Embedding transient error (attempt %d/%d), waiting %.1fs: %s",
                attempt + 1, max_retries, wait, exc,
            )
            time.sleep(wait)
            attempt += 1

    logger.error("Embedding failed after %d retries: %s", max_retries, last_error)
    return None
