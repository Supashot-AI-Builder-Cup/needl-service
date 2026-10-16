import asyncio
import io
import os
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.config import MIB, load_settings
from main import create_app

RISKY_LINK = "https://shopee.co.id/Dermalune-10-Niacinamide-Alpha-Arbutin-Brightening-Serum-30ml-Original-i.773019284.25810442671"
SAFE_LINK = "https://shopee.co.id/Dermalune-Niacinamide-Brightening-Serum-30ml-Official-i.218334561.24019837712"
JOBS = "/api/v1/verification-jobs"
TEST_PROJECT = "needl-test"
UI_ORIGIN = "http://localhost:5173"
NOT_RUNNING = (
    "Firestore emulator is not running. Start it with scripts/firestore_emulator.sh "
    "or run the tests with scripts/test.sh."
)


@pytest.fixture(scope="session")
def firestore_emulator() -> str:
    """Emulator host. Fails instead of letting a test reach a real Firestore project."""
    host = os.environ.get("FIRESTORE_EMULATOR_HOST", "").strip()
    if not host:
        pytest.fail(NOT_RUNNING, pytrace=False)
    try:
        httpx.get(f"http://{host}/", timeout=2)
    except httpx.HTTPError:
        pytest.fail(f"{NOT_RUNNING} (FIRESTORE_EMULATOR_HOST={host})", pytrace=False)
    return host


def clear_emulator(host: str) -> None:
    res = httpx.delete(
        f"http://{host}/emulator/v1/projects/{TEST_PROJECT}/databases/(default)/documents", timeout=10
    )
    res.raise_for_status()


@pytest.fixture
def settings(tmp_path: Path, firestore_emulator: str):
    clear_emulator(firestore_emulator)
    return load_settings().with_overrides(
        firestore_project=TEST_PROJECT,
        firestore_database="(default)",
        upload_dir=tmp_path / "uploads",
        source="stub",
        analyzer="stub",
        stub_step_ms=0,
        worker_count=1,
        create_rate_per_min=1000,
        poll_rate_per_min=1000,
        trust_forwarded_for=False,
        legacy_routes=True,
        cors_origins=[UI_ORIGIN],
        max_upload_total_bytes=30 * MIB,
        idempotency_ttl_s=24 * 3600,
        job_stale_after_s=240,
        job_sweep_interval_s=60,
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
