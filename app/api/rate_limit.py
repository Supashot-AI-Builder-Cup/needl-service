"""In-memory sliding-window rate limit per client IP. Fine for a single process."""

import math
import time
from collections import deque

from fastapi import Request

from .errors import ApiError

WINDOW_S = 60.0


class RateLimiter:
    def __init__(self, limits: dict[str, int], trust_forwarded_for: bool = False):
        self.limits = limits
        self.trust_forwarded_for = trust_forwarded_for
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._calls = 0

    def client_ip(self, request: Request) -> str:
        if self.trust_forwarded_for:
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def check(self, request: Request, bucket: str) -> None:
        limit = self.limits[bucket]
        now = time.monotonic()
        hits = self._hits.setdefault((bucket, self.client_ip(request)), deque())
        while hits and now - hits[0] >= WINDOW_S:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, math.ceil(WINDOW_S - (now - hits[0])))
            raise ApiError(
                429,
                "rate_limited",
                "Too many requests. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)
        self._calls += 1
        if self._calls % 500 == 0:
            self._prune(now)

    def _prune(self, now: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= WINDOW_S]:
            del self._hits[key]
