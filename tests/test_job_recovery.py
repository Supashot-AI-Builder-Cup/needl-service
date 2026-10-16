"""Claiming jobs and sweeping up jobs left behind by a dead worker."""

import asyncio
import uuid
from datetime import timedelta

import httpx
import pytest
from google.cloud.firestore_v1.async_transaction import AsyncTransaction

from app.db.models import JobRecord, utc_now
from main import create_app
from tests.conftest import JOBS, RISKY_LINK, wait_for_job

LONG_AGO = timedelta(hours=1)


def job_record(**changes) -> JobRecord:
    now = utc_now()
    fields = dict(
        id=str(uuid.uuid4()),
        status="pending",
        input_kind="url",
        input_url=RISKY_LINK,
        locale="en-US",
        created_at=now,
        updated_at=now,
    )
    fields.update(changes)
    return JobRecord(**fields)


@pytest.fixture
async def sweeping_client(settings):
    """App whose sweeper runs every 50 ms and treats jobs idle for 1 s as stale."""
    app = create_app(settings.with_overrides(job_sweep_interval_s=0.05, job_stale_after_s=1))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield app, client


async def test_fresh_processing_job_is_left_to_its_worker(app):
    job = job_record(status="processing", attempts=1)
    await app.state.repo.insert_job(job)
    await app.state.queue.put(job.id)
    await app.state.queue.join()
    stored = await app.state.repo.get_job(job.id)
    assert (stored.status, stored.attempts) == ("processing", 1)


async def test_sweeper_resumes_stale_job(sweeping_client):
    app, client = sweeping_client
    old = utc_now() - LONG_AGO
    job = job_record(status="processing", attempts=1, created_at=old, updated_at=old)
    await app.state.repo.insert_job(job)
    data = await wait_for_job(client, job.id)
    assert data["status"] == "completed"
    assert (await app.state.repo.get_job(job.id)).attempts == 2


async def test_stale_screenshot_job_without_uploads_fails(sweeping_client, tmp_path):
    app, client = sweeping_client
    old = utc_now() - LONG_AGO
    job = job_record(
        status="processing",
        input_kind="screenshots",
        input_url=None,
        input={"dir": str(tmp_path / "gone"), "slots": {"product": "a.png", "store": "b.png"}, "extras": []},
        attempts=1,
        created_at=old,
        updated_at=old,
    )
    await app.state.repo.insert_job(job)
    data = await wait_for_job(client, job.id)
    assert data["status"] == "failed"
    assert data["error"]["code"] == "interrupted"


async def test_fresh_pending_job_is_not_swept(sweeping_client):
    app, _ = sweeping_client
    job = job_record()
    await app.state.repo.insert_job(job)
    await asyncio.sleep(0.3)
    assert (await app.state.repo.get_job(job.id)).status == "pending"


async def test_job_put_twice_runs_once(app, monkeypatch):
    runner = app.state.queue.runner
    calls: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_run(job_id: str) -> None:
        calls.append(job_id)
        started.set()
        await release.wait()

    monkeypatch.setattr(runner, "run", slow_run)
    await app.state.queue.put("job-1")
    await started.wait()
    await app.state.queue.put("job-1")
    release.set()
    await app.state.queue.join()
    assert calls == ["job-1"]


async def test_shutdown_during_claim_does_not_hang(app, monkeypatch):
    # Cancelled while the claim transaction begins, the Firestore library raises ValueError
    # (nothing to roll back) instead of CancelledError. The worker must still stop.
    started = asyncio.Event()

    async def slow_begin(self, retry_id=None):
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(AsyncTransaction, "_begin", slow_begin)
    job = job_record()
    await app.state.repo.insert_job(job)
    await app.state.queue.put(job.id)
    await asyncio.wait_for(started.wait(), timeout=5)
    await asyncio.wait_for(app.state.queue.stop(), timeout=5)
    assert (await app.state.repo.get_job(job.id)).status == "pending"


async def test_new_jobs_still_run_normally(client):
    res = await client.post(JOBS, json={"url": RISKY_LINK})
    assert (await wait_for_job(client, res.json()["data"]["id"]))["status"] == "completed"
