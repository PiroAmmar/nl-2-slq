"""
pipeline/llm.py — Single Groq LLM wrapper for all pipeline steps.

All stages (triage, selection, SQL generation, verification, response)
go through call_llm(). Per-question call budget enforced here.
"""

from __future__ import annotations

import logging
import os
import time
import random

from groq import Groq

logger = logging.getLogger(__name__)

GROQ_MODEL = "openai/gpt-oss-120b"

_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _client


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

def call_llm(
    *,
    messages: list[dict[str, str]],
    step: str,
    budget: CallBudget,
    max_tokens: int = 1024,
    temperature: float = 0.1,
    max_retries: int = 4,
) -> str:
    """
    Call Groq and return the response text.

    Retries on 429/5xx with exponential backoff + jitter.
    Respects Retry-After header when present.
    Raises RuntimeError on budget exhaustion or permanent failure.
    """
    budget.consume(step)
    client = _get_client()

    attempt = 0
    base_wait = 1.0
    last_error: Exception | None = None

    while attempt <= max_retries:
        try:
            completion = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_completion_tokens=max_tokens,
                top_p=0.95,
                reasoning_effort="low",
                stream=False,
                stop=None,
            )
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
            time.sleep(wait)
            attempt += 1

    raise RuntimeError(
        f"[{step}] Groq call failed after {max_retries} retries: {last_error}"
    )
