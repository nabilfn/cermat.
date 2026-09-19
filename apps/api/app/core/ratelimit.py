"""Small in-process sliding-window rate limiter.

Protects AI-backed endpoints (cost) and sign-in (guessing). State is per API
process: with several replicas each enforces its own window, which is an
accepted limitation at this scale — see docs/security.md.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from app.core.errors import ApiError


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, *, limit: int, window_seconds: float = 60.0) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                retry = max(1, int(window_seconds - (now - hits[0])) + 1)
                raise ApiError(
                    429,
                    "RATE_LIMITED",
                    "Too many requests. Please wait a moment and try again.",
                    headers={"Retry-After": str(retry)},
                )
            hits.append(now)
            if len(self._hits) > 10_000:  # bound memory
                for stale in [k for k, v in self._hits.items() if not v or v[-1] <= now - window_seconds][:5000]:
                    self._hits.pop(stale, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()
