"""Live sources without credentials fail cleanly instead of crashing."""

import httpx
import pytest

from main import create_app
from tests.conftest import JOBS, RISKY_LINK, image_bytes, wait_for_job


@pytest.fixture
async def live_client(settings, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    app = create_app(settings.with_overrides(source="live", apify_token=None, rapidapi_key=None))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def test_link_without_any_provider_fails_with_source_unavailable(live_client):
    res = await live_client.post(JOBS, json={"url": RISKY_LINK})
    data = await wait_for_job(live_client, res.json()["data"]["id"])
    assert data["status"] == "failed"
    assert data["error"]["code"] == "source_unavailable"
    assert data["preview"] is None


async def test_screenshots_without_gemini_fail_with_extraction_failed(live_client, settings):
    res = await live_client.post(
        JOBS,
        files={
            "product": ("p.png", image_bytes(), "image/png"),
            "store": ("s.png", image_bytes(), "image/png"),
        },
    )
    data = await wait_for_job(live_client, res.json()["data"]["id"])
    assert data["status"] == "failed"
    assert data["error"]["code"] == "extraction_failed"
    assert not any(settings.upload_dir.rglob("*.*"))
