"""In-memory sliding-window rate limiter.

Thread-safe and dependency-free. Scope is per-process: fine for the single-node
MVP; a multi-instance deployment would swap the same interface for Redis
(``INCR`` + ``EXPIRE`` or a Lua sliding window) without touching call sites.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, ts: float | None = None) -> tuple[bool, float]:
        """Record a request. Returns (allowed, retry_after_seconds)."""
        ts = ts if ts is not None else time.monotonic()
        with self._lock:
            bucket = self._hits.get(key)
            if bucket is None:
                bucket = deque()
                self._hits[key] = bucket
            cutoff = ts - self.window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(0.0, bucket[0] + self.window - ts)
                return False, retry_after
            bucket.append(ts)
            # Opportunistic cleanup: drop emptied buckets so memory does not
            # grow with unique client keys over the process lifetime.
            if len(bucket) == 1 and len(self._hits) > 10_000:
                stale = [k for k, v in self._hits.items() if not v]
                for k in stale:
                    self._hits.pop(k, None)
            return True, 0.0


# Global API budget per client (requests / minute). Health checks are exempt.
api_limiter = SlidingWindowLimiter(limit=240, window_seconds=60.0)
# Login is a brute-force target: much tighter budget per client.
login_limiter = SlidingWindowLimiter(limit=10, window_seconds=60.0)


def client_key(request) -> str:
    """Identity for rate limiting: forwarded client IP, else socket peer."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
