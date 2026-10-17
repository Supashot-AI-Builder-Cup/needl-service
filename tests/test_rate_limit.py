"""Client IP behind proxies, and turning the legacy routes off."""

import httpx
import pytest
from starlette.requests import Request

from app.api.rate_limit import RateLimiter
from main import create_app
from tests.conftest import JOBS


def request_with(forwarded: str | None, client_host: str = "10.0.0.1") -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "client": (client_host, 1234)})


@pytest.mark.parametrize(
    ("forwarded", "hops", "expected"),
    [
        ("1.1.1.1, 9.9.9.9", 1, "9.9.9.9"),
        ("1.1.1.1, 9.9.9.9", 2, "1.1.1.1"),
        ("spoofed,, 9.9.9.9 ", 1, "9.9.9.9"),
        ("9.9.9.9", 2, "10.0.0.1"),
        ("", 1, "10.0.0.1"),
        (None, 1, "10.0.0.1"),
    ],
)
def test_client_ip_counts_trusted_hops_from_the_right(forwarded, hops, expected):
    limiter = RateLimiter({}, trust_forwarded_for=True, trusted_proxy_hops=hops)
    assert limiter.client_ip(request_with(forwarded)) == expected


def test_forwarded_for_is_ignored_unless_trusted():
    assert RateLimiter({}).client_ip(request_with("1.1.1.1, 9.9.9.9")) == "10.0.0.1"


async def test_spoofed_first_entries_do_not_escape_the_limit(settings):
    app = create_app(settings.with_overrides(trust_forwarded_for=True, trusted_proxy_hops=1, create_rate_per_min=10))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            statuses = []
            for i in range(11):
                # An empty body is rejected (422) but still counted by the limiter; no job is created.
                res = await client.post(JOBS, json={}, headers={"X-Forwarded-For": f"198.51.100.{i}, 203.0.113.9"})
                statuses.append(res.status_code)
    assert statuses[:10] == [422] * 10
    assert statuses[10] == 429


async def test_legacy_routes_can_be_turned_off(settings):
    app = create_app(settings.with_overrides(legacy_routes=False))
    paths = set(app.openapi()["paths"])
    assert "/api/v1/verification-jobs" in paths
    assert not paths & {"/scrape", "/analyze", "/analyze-image"}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post("/scrape", json={"url": "https://shopee.co.id/x"})
    assert res.status_code == 404
