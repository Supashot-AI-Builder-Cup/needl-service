"""In-memory sliding-window rate limit per client IP. Fine for a single process.

Behind proxies (Cloud Run, a load balancer) every request comes from the proxy, so the
client IP is read from X-Forwarded-For. Clients can send their own X-Forwarded-For;
proxies append to it, so only the entries added by trusted proxies (counted from the
right) are reliable.
"""

import math
import time
from collections import deque

from fastapi import Request

from .errors import ApiError

WINDOW_S = 60.0


class RateLimiter:
    def __init__(self, limits: dict[str, int], trust_forwarded_for: bool = False, trusted_proxy_hops: int = 1):
        self.limits = limits
        self.trust_forwarded_for = trust_forwarded_for
        self.trusted_proxy_hops = trusted_proxy_hops
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._calls = 0

    def client_ip(self, request: Request) -> str:
        if self.trust_forwarded_for:
            forwarded = request.headers.get("x-forwarded-for", "")
            entries = [part.strip() for part in forwarded.split(",") if part.strip()]
            if len(entries) >= self.trusted_proxy_hops:
                return entries[-self.trusted_proxy_hops]
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
