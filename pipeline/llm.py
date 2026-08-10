"""
pipeline/llm.py — Single async Groq LLM wrapper for all pipeline steps.

All stages (triage, selection, SQL generation, verification, response)
go through call_llm(). Per-question call budget enforced here.

Async end-to-end: retry backoff uses asyncio.sleep (never blocks a thread),
and a process-wide RateLimiter throttles outbound requests to stay under
Groq's RPM ceiling regardless of how many questions/jobs run concurrently.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time

from groq import AsyncGroq

logger = logging.getLogger(__name__)

GROQ_MODEL = os.getenv("GROQ_MODEL_NAME", "openai/gpt-oss-120b")

# Groq free/dev tier is commonly RPM-limited well below what a naive loop
# sends. Override via env for paid tiers. This is a *ceiling*, not a target —
# keeping it conservative is what actually stops the 429 storm.
GROQ_RPM_LIMIT = int(os.getenv("GROQ_RPM_LIMIT", "28"))

_client: AsyncGroq | None = None


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        _client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
    return _client


# ── process-wide rate limiter ──────────────────────────────────────────────
# Sliding-window limiter shared by every caller in this process (interactive
# /query requests AND background doc_qa jobs). This is what actually keeps
# us under Groq's RPM — a per-question sleep does not, because concurrent
# jobs/requests each apply their own delay independently.

class RateLimiter:
    """Async sliding-window rate limiter: at most `limit` calls per `period`s."""

    def __init__(self, limit: int, period: float = 60.0):
        self.limit = limit
        self.period = period
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._timestamps = [t for t in self._timestamps if now - t < self.period]
                if len(self._timestamps) < self.limit:
                    self._timestamps.append(now)
                    return
                wait = self.period - (now - self._timestamps[0])
                logger.debug("[rate-limiter] at capacity, waiting %.2fs", wait)
                # Lock stays held across the sleep on purpose: admission is
                # serialized once we're at capacity, so callers queue up and
                # each re-checks the window on wake rather than stampeding.
                await asyncio.sleep(max(wait, 0.05))


_rate_limiter = RateLimiter(GROQ_RPM_LIMIT)


# ── per-question call budget ──────────────────────────────────────────────────

class CallBudget:
    """Cap total LLM calls per user question to prevent runaway usage."""

    def __init__(self, max_calls: int = 8):
        self.max_calls = max_calls
        self.used = 0

    def consume(self, step: str) -> None:
        self.used += 1
        if self.used > self.max_calls:
            raise RuntimeError(
                f"[{step}] Call budget exhausted ({self.max_calls} calls/question)."
            )

    def remaining(self) -> int:
        return max(0, self.max_calls - self.used)


# ── core wrapper ──────────────────────────────────────────────────────────────

async def call_llm(
    *,
    messages: list[dict[str, str]],
    step: str,
    budget: CallBudget,
    max_tokens: int = 1024,
    temperature: float = 0.1,
    max_retries: int = 4,
) -> str:
    """
    Call Groq (async) and return the response text.

    Retries on 429/5xx with exponential backoff + jitter, via asyncio.sleep
    so a blocked retry never ties up an OS thread. Respects Retry-After
    when present. Every call passes through the shared RateLimiter first.
    Raises RuntimeError on budget exhaustion or permanent failure.
    """
    budget.consume(step)
    client = _get_client()

    attempt = 0
    base_wait = 1.0
    last_error: Exception | None = None

    while attempt <= max_retries:
        await _rate_limiter.acquire()
        try:
            # We pass reasoning_effort if the model supports it. 
            params = {
                "model": GROQ_MODEL,
                "messages": messages,  # type: ignore[arg-type]
                "temperature": temperature,
                "max_completion_tokens": max_tokens,
                "top_p": 0.85,
                "stream": False,
                "stop": None,
            }
            completion = await client.chat.completions.create(**params)
            text = (completion.choices[0].message.content or "").strip()
            logger.info("[%s] Groq call #%d OK (%d chars)", step, budget.used, len(text))
            return text

        except Exception as exc:
            last_error = exc
            err_str = str(exc)

            # Honour Retry-After header if present
            retry_after: float | None = None
            resp = getattr(exc, "response", None)
            if resp is not None:
                ra = getattr(resp, "headers", {}).get("Retry-After")
                if ra:
                    try:
                        retry_after = float(ra)
                    except ValueError:
                        pass

            is_transient = any(tok in err_str for tok in ("429", "500", "503", "rate", "quota"))
            if not is_transient:
                logger.error("[%s] Groq non-retryable error: %s", step, exc)
                raise

            wait = retry_after or (base_wait * (2 ** attempt) + random.uniform(0, 1))
            logger.warning(
                "[%s] Groq transient error (attempt %d/%d), waiting %.1fs: %s",
                step, attempt + 1, max_retries, wait, exc,
            )
            await asyncio.sleep(wait)
            attempt += 1

    raise RuntimeError(
        f"[{step}] Groq call failed after {max_retries} retries: {last_error}"
    )
