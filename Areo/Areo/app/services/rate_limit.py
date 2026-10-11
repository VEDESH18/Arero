"""A small in-memory sliding-window rate limiter.

Used on the public /verify endpoints so verification codes cannot be
brute-forced quickly. State lives in this process only, which matches the
single-process deployment of this app; behind several processes or servers,
replace it with a shared store (e.g. Redis) or a reverse-proxy rule
(nginx `limit_req`) keyed on the client IP.
"""

import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> float | None:
        """Record one request. Returns None if allowed, else the seconds to wait before retrying."""
        if self.limit <= 0:
            return None
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(hits[0] + self.window - now, 0.0)
            hits.append(now)
            if len(self._hits) > 10_000:  # keep memory bounded: forget idle clients
                self._prune(now)
            return None

    def _prune(self, now: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
            del self._hits[key]
