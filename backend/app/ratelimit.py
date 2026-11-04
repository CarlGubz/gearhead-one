"""
Tiny in-process sliding-window rate limiter.

Good enough for a single backend replica. If you scale the API horizontally,
swap `RateLimiter.hit` for a Redis-backed counter (see docs/DEPLOYMENT.md);
per-account lockout in the database already works across replicas.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Record a hit; return False if the caller is over the limit."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 10_000:  # opportunistic cleanup
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
                    self._hits.pop(k, None)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def limit_dependency(limiter: RateLimiter, scope: str):
    def _dep(request: Request):
        if not limiter.hit(f"{scope}:{client_ip(request)}"):
            raise HTTPException(429, "Too many attempts. Please wait a minute and try again.")
    return _dep
