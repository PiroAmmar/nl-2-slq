"""
pipeline/llm.py — Single async Groq LLM wrapper for all pipeline steps.

All stages (triage, selection, SQL generation, verification, response)
go through call_llm(). Per-question call budget enforced here.

Async end-to-end: retry backoff uses asyncio.sleep (never blocks a thread),
and a process-wide RateLimiter throttles outbound requests to stay under
Groq's RPM ceiling regardless of how many questions/jobs run concurrently.

Priority tiers:
  "interactive" — real-time user queries (admitted first)
  "background"  — golden-gen and doc-ingest batch jobs (admitted after
                   interactive, but never starved thanks to aging counter)

The BACKGROUND_JOB_SEMAPHORE caps how many background *jobs* (golden-gen,
doc-ingest) run concurrently at the process level, independently of both
the RPM limiter and doc_ingestor's per-job _MAX_CONCURRENT_QUESTIONS
semaphore (which bounds per-job question concurrency, not cross-job
concurrency). Both semaphores can and should coexist.
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
#
# Priority model: two asyncio.Condition objects share one underlying Lock.
# Interactive waiters are notified before background waiters.
#
# Anti-starvation aging: after _AGING_THRESHOLD consecutive interactive
# admissions while at least one background waiter is queued, the next
# admission goes to background regardless of pending interactive traffic.
# This guarantees background jobs make forward progress under sustained load.

_AGING_THRESHOLD = 5  # max consecutive interactive admits before forcing one background


class RateLimiter:
    """Async sliding-window rate limiter with two priority tiers.

    Priority values: "interactive" (default) or "background".
    Interactive callers are admitted before background callers, but background
    callers are never starved: after _AGING_THRESHOLD consecutive interactive
    admissions while a background waiter is present, the next slot goes to
    background.

    Observability counters (all cumulative):
      .hits                        — total calls admitted
      .throttled_count             — total waits across all callers
      .interactive_throttled_count — waits by interactive callers
      .background_throttled_count  — waits by background callers
    """

    def __init__(self, limit: int, period: float = 60.0):
        self.limit = limit
        self.period = period
        self._timestamps: list[float] = []

        # Single lock shared by both conditions.
        # asyncio.Condition(lock) uses the supplied lock as its underlying
        # primitive. cond.wait() releases and re-acquires it atomically,
        # so holding `self._lock` via `async with self._lock:` before calling
        # cond.wait() is correct standard usage.
        self._lock = asyncio.Lock()
        self._interactive_cond = asyncio.Condition(self._lock)
        self._background_cond = asyncio.Condition(self._lock)

        # Waiter counts (guarded by _lock)
        self._interactive_waiting: int = 0
        self._background_waiting: int = 0

        # Anti-starvation counter: number of consecutive interactive admissions
        # while background was also waiting. Reset to 0 on any background admission.
        self._consecutive_interactive: int = 0

        # Observability counters (incremented once per caller that waits,
        # not once per loop iteration)
        self.hits: int = 0
        self.throttled_count: int = 0
        self.interactive_throttled_count: int = 0
        self.background_throttled_count: int = 0

    async def acquire(self, priority: str = "interactive") -> None:
        """Acquire one rate-limit slot.

        Blocks until the sliding window has room, respecting priority order
        with aging-based anti-starvation for background waiters.
        """
        is_background = priority == "background"
        cond = self._background_cond if is_background else self._interactive_cond
        _throttle_counted = False  # track whether we already incremented counters

        async with self._lock:
            if is_background:
                self._background_waiting += 1
            else:
                self._interactive_waiting += 1

            try:
                while True:
                    now = time.monotonic()
                    self._timestamps = [
                        t for t in self._timestamps if now - t < self.period
                    ]
                    has_capacity = len(self._timestamps) < self.limit

                    if has_capacity and self._should_admit(is_background):
                        # ── Admit this caller ─────────────────────────────
                        self._timestamps.append(now)
                        self.hits += 1
                        # Update anti-starvation counter BEFORE notifying next,
                        # because _notify_next reads _consecutive_interactive.
                        if is_background:
                            self._consecutive_interactive = 0
                        elif self._background_waiting > 0:
                            # Interactive admitted while background also waiting
                            self._consecutive_interactive += 1
                        self._notify_next()
                        return

                    # ── Must wait (either at capacity or yielding to other tier) ──
                    # Count each caller only once even if it loops multiple times.
                    if not _throttle_counted:
                        _throttle_counted = True
                        self.throttled_count += 1
                        if is_background:
                            self.background_throttled_count += 1
                        else:
                            self.interactive_throttled_count += 1

                    if not has_capacity:
                        # At capacity: sleep inside the lock (serializes admission)
                        # so all callers queue and each re-checks on wake rather
                        # than stampeding.
                        wait = self.period - (now - self._timestamps[0])
                        logger.debug(
                            "[rate-limiter] at capacity (%s), waiting %.2fs",
                            priority, wait,
                        )
                        await asyncio.sleep(max(wait, 0.05))
                    else:
                        # Capacity available but must yield to higher-priority tier;
                        # sleep until notified by the admitted caller.
                        await cond.wait()

            finally:
                if is_background:
                    self._background_waiting -= 1
                else:
                    self._interactive_waiting -= 1

    def _should_admit(self, is_background: bool) -> bool:
        """Return True if this caller's tier should take the available slot now."""
        if is_background:
            # Background is admitted when no interactive waiters exist,
            # or when the aging threshold has been reached (anti-starvation).
            return (
                self._interactive_waiting == 0
                or self._consecutive_interactive >= _AGING_THRESHOLD
            )
        # Interactive is always admitted when capacity exists.
        return True

    def _notify_next(self) -> None:
        """Wake up the next waiter after a slot has been taken.

        Prefers interactive unless the aging threshold is already reached,
        in which case background is preferred to enforce the anti-starvation
        guarantee.  Called while _lock is held (required for cond.notify()).
        """
        if (
            self._interactive_waiting > 0
            and self._consecutive_interactive < _AGING_THRESHOLD
        ):
            self._interactive_cond.notify()
        elif self._background_waiting > 0:
            self._background_cond.notify()
        elif self._interactive_waiting > 0:
            # Fallback: no background waiters, still wake interactive
            self._interactive_cond.notify()


_rate_limiter = RateLimiter(GROQ_RPM_LIMIT)


# ── background-job concurrency cap ────────────────────────────────────────────
# Bounds how many background *jobs* (golden-gen and doc-ingest) run
# process-wide at once. Independent of the RPM limiter above and independent
# of doc_ingestor.py's _MAX_CONCURRENT_QUESTIONS semaphore (which bounds
# per-job question concurrency within a single batch job).
#
# BEHAVIOR CHANGE vs. pre-v3: with the default GOLDEN_GEN_MAX_CONCURRENT=1,
# only one background job of either kind runs at a time — a doc-ingest job
# and a golden-gen job can no longer run concurrently, whereas before they
# could (bounded only by the shared RPM limiter). This is intentional.
# Raise GOLDEN_GEN_MAX_CONCURRENT to allow concurrent background jobs if needed.
BACKGROUND_JOB_SEMAPHORE = asyncio.Semaphore(
    int(os.getenv("GOLDEN_GEN_MAX_CONCURRENT", "1"))
)


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
    priority: str = "interactive",
) -> str:
    """
    Call Groq (async) and return the response text.

    Retries on 429/5xx with exponential backoff + jitter, via asyncio.sleep
    so a blocked retry never ties up an OS thread. Respects Retry-After
    when present. Every call passes through the shared RateLimiter first.
    Raises RuntimeError on budget exhaustion or permanent failure.

    priority: "interactive" (default) or "background". Interactive callers
    are admitted before background callers at capacity, subject to the
    anti-starvation aging rule in RateLimiter.
    """
    budget.consume(step)
    client = _get_client()

    attempt = 0
    base_wait = 1.0
    last_error: Exception | None = None

    while attempt <= max_retries:
        await _rate_limiter.acquire(priority=priority)
        try:
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
