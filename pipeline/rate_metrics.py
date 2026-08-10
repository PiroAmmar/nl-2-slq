"""
pipeline/rate_metrics.py — Lightweight observability accessor for rate-limiter stats.

Exposes get_rate_stats() so callers (e.g. backend/routers/dashboard.py) can
surface real-time counters without reaching into pipeline.llm internals directly.
No new HTTP route is wired here — that is optional and out of scope for this pass.
"""

from __future__ import annotations


def get_rate_stats() -> dict:
    """Return a snapshot of the shared RateLimiter's observability counters.

    Keys returned:
      hits                        — total LLM calls admitted since process start
      throttled_count             — total times any caller had to wait
      interactive_throttled_count — waits by interactive (query) callers
      background_throttled_count  — waits by background (golden-gen / doc-ingest) callers
      interactive_waiting         — callers currently queued at the interactive tier
      background_waiting          — callers currently queued at the background tier
      consecutive_interactive     — current anti-starvation aging counter
                                    (resets to 0 on each background admission)
    """
    # Import here to avoid circular imports at module load time.
    from pipeline.llm import _rate_limiter

    return {
        "hits": _rate_limiter.hits,
        "throttled_count": _rate_limiter.throttled_count,
        "interactive_throttled_count": _rate_limiter.interactive_throttled_count,
        "background_throttled_count": _rate_limiter.background_throttled_count,
        "interactive_waiting": _rate_limiter._interactive_waiting,
        "background_waiting": _rate_limiter._background_waiting,
        "consecutive_interactive": _rate_limiter._consecutive_interactive,
    }
