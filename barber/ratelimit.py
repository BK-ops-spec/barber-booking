from __future__ import annotations

import threading
import time
from collections import deque


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int = 60, message: str = "Too many requests, please try again shortly"):
        super().__init__(message)
        self.retry_after = retry_after
        self.message = message


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int, max_keys: int = 10000):
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        expired = [key for key, stamps in self._hits.items() if not stamps or now - stamps[-1] >= self.window]
        for key in expired:
            self._hits.pop(key, None)

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) >= self.max_keys:
                self._prune(now)
            if len(self._hits) >= self.max_keys:
                oldest = min(self._hits, key=lambda k: self._hits[k][-1] if self._hits[k] else 0)
                self._hits.pop(oldest, None)
            self._hits.setdefault(key, deque()).append(now)

    def count(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            stamps = self._hits.get(key)
            if not stamps:
                return 0
            recent = [s for s in stamps if now - s < self.window]
            if recent:
                self._hits[key] = deque(recent)
            else:
                self._hits.pop(key, None)
            return len(recent)

    def retry_after(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            stamps = self._hits.get(key)
            if not stamps:
                return 0
            fresh = [s for s in stamps if now - s < self.window]
            if not fresh:
                return 0
            return max(1, int(self.window - (now - fresh[0])) + 1)

    def check(self, key: str) -> None:
        if self.count(key) >= self.limit:
            raise RateLimitExceeded(self.retry_after(key))

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
