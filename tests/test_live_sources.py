"""Live sources without credentials fail cleanly instead of crashing."""

import httpx
import pytest

from app.llm import gemini
from main import create_app
from tests.conftest import JOBS, RISKY_LINK, image_bytes, wait_for_job

SCREENSHOTS = {
    "product": ("p.png", image_bytes(), "image/png"),
    "store": ("s.png", image_bytes(), "image/png"),
}


@pytest.fixture
async def live_client(settings, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    app = create_app(settings.with_overrides(source="live", apify_token=None, rapidapi_key=None))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def run_screenshot_job(client) -> dict:
    res = await client.post(JOBS, files=SCREENSHOTS)
    return await wait_for_job(client, res.json()["data"]["id"])


async def test_link_without_any_provider_fails_with_source_unavailable(live_client):
    res = await live_client.post(JOBS, json={"url": RISKY_LINK})
    data = await wait_for_job(live_client, res.json()["data"]["id"])
    assert data["status"] == "failed"
    assert data["error"]["code"] == "source_unavailable"
    assert data["preview"] is None


async def test_screenshots_without_gemini_fail_with_service_busy(live_client, settings):
    # Not configured is our problem, not the user's screenshots.
    data = await run_screenshot_job(live_client)
    assert data["status"] == "failed"
    assert data["error"]["code"] == "service_busy"
    assert not any(settings.upload_dir.rglob("*.*"))


async def test_unreadable_gemini_answer_is_extraction_failed(live_client, monkeypatch):
    async def bad_answer(*args, **kwargs):
        raise ValueError("answer does not match the schema")

    monkeypatch.setattr(gemini, "generate_structured", bad_answer)
    data = await run_screenshot_job(live_client)
    assert data["error"]["code"] == "extraction_failed"


async def test_gemini_api_error_is_service_busy(live_client, monkeypatch):
    async def overloaded(*args, **kwargs):
        raise ConnectionError("503 UNAVAILABLE")

    monkeypatch.setattr(gemini, "generate_structured", overloaded)
    data = await run_screenshot_job(live_client)
    assert data["error"]["code"] == "service_busy"
    assert "503" not in data["error"]["message"]
