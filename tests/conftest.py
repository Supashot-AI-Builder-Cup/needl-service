import asyncio
import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.config import load_settings
from main import create_app

RISKY_LINK = "https://shopee.co.id/Dermalune-10-Niacinamide-Alpha-Arbutin-Brightening-Serum-30ml-Original-i.773019284.25810442671"
SAFE_LINK = "https://shopee.co.id/Dermalune-Niacinamide-Brightening-Serum-30ml-Official-i.218334561.24019837712"
JOBS = "/api/v1/verification-jobs"


@pytest.fixture
def settings(tmp_path: Path):
    return load_settings().with_overrides(
        db_path=tmp_path / "needl.db",
        upload_dir=tmp_path / "uploads",
        source="stub",
        analyzer="stub",
        stub_step_ms=0,
        worker_count=1,
        create_rate_per_min=1000,
        poll_rate_per_min=1000,
    )


@pytest.fixture
async def app(settings):
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def image_bytes(fmt: str = "PNG", size=(40, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format=fmt)
    return buf.getvalue()


async def wait_for_job(client: httpx.AsyncClient, job_id: str, timeout: float = 5.0) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        res = await client.get(f"{JOBS}/{job_id}")
        data = res.json()["data"]
        if data["status"] in ("completed", "failed") or loop.time() > deadline:
            return data
        await asyncio.sleep(0.02)
