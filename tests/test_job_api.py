import uuid
from datetime import timedelta

import httpx
import pytest

from app.contract.jobs import VerificationJob
from app.db.models import JobRecord, utc_now
from main import create_app
from tests.conftest import JOBS, RISKY_LINK, SAFE_LINK, wait_for_job


async def test_unknown_job_is_404_envelope(client):
    res = await client.get(f"{JOBS}/{uuid.uuid4()}")
    assert res.status_code == 404
    detail = res.json()["detail"]
    assert detail["code"] == "job_not_found"
    assert detail["message"] == "Verification job was not found."
    assert detail["request_id"] == res.headers["x-request-id"]


async def test_invalid_job_id_is_422(client):
    res = await client.get(f"{JOBS}/not-a-uuid")
    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["path", "job_id"]


async def test_idempotency_replay_and_conflict(client):
    headers = {"Idempotency-Key": "scan-123"}
    first = await client.post(JOBS, json={"url": RISKY_LINK}, headers=headers)
    assert first.status_code == 202
    job_id = first.json()["data"]["id"]
    await wait_for_job(client, job_id)

    replay = await client.post(JOBS, json={"url": RISKY_LINK}, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["data"]["id"] == job_id
    assert replay.json()["data"]["status"] == "completed"
    assert "retry-after" not in replay.headers

    conflict = await client.post(JOBS, json={"url": SAFE_LINK}, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"


async def test_bad_idempotency_key_is_400(client):
    res = await client.post(JOBS, json={"url": RISKY_LINK}, headers={"Idempotency-Key": "x" * 129})
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "invalid_idempotency_key"


async def test_rate_limit(app, client):
    app.state.limiter.limits["create"] = 2
    for _ in range(2):
        assert (await client.post(JOBS, json={"url": RISKY_LINK})).status_code == 202
    limited = await client.post(JOBS, json={"url": RISKY_LINK})
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "rate_limited"
    assert int(limited.headers["retry-after"]) >= 1


async def test_pending_job_shape(app, client):
    # A job inserted without being queued stays pending.
    now = utc_now()
    job_id = str(uuid.uuid4())
    await app.state.repo.insert_job(
        JobRecord(id=job_id, status="pending", input_kind="url", input_url=RISKY_LINK, locale="en-US",
                  created_at=now, updated_at=now)
    )
    res = await client.get(f"{JOBS}/{job_id}")
    data = res.json()["data"]
    assert res.headers["retry-after"] == "2"
    assert data["progress"] == 0
    assert data["preview"] is None and data["verification"] is None and data["error"] is None


async def test_verification_detail_uses_saved_firestore_result(client):
    created = await client.post(JOBS, json={"url": RISKY_LINK, "locale": "id-ID"})
    assert created.status_code == 202
    job = await wait_for_job(client, created.json()["data"]["id"])
    assert job["status"] == "completed"
    verification_id = str(job["verification_id"])

    detail = await client.get(f"/api/v1/verifications/{verification_id}")
    assert detail.status_code == 200, detail.text
    assert str(detail.json()["data"]["id"]) == verification_id


async def test_history_items_carry_the_product_image(app, client):
    from tests.test_repository import make_result

    photo = "https://down-id.img.susercontent.com/file/id-11134207-example"
    result = await make_result()
    with_photo = result.model_copy(update={"product": result.product.model_copy(update={"image": photo})})
    await app.state.repo.insert_verification(with_photo)
    without_photo = await make_result(kind="screenshots")
    await app.state.repo.insert_verification(without_photo)

    res = await client.get("/api/v1/verifications")
    assert res.status_code == 200, res.text
    images = {item["id"]: item["image"] for item in res.json()["data"]}
    assert images == {str(with_photo.id): photo, str(without_photo.id): None}


async def test_expired_idempotency_key_starts_a_new_job(settings):
    app = create_app(settings.with_overrides(idempotency_ttl_s=0))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Idempotency-Key": "scan-expiring"}
            first = await client.post(JOBS, json={"url": RISKY_LINK}, headers=headers)
            second = await client.post(JOBS, json={"url": SAFE_LINK}, headers=headers)
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["data"]["id"] != second.json()["data"]["id"]


async def test_job_that_keeps_getting_interrupted_fails(app, client, settings):
    # Last touched long ago: its worker is gone, so it may be claimed, but no attempts are left.
    old = utc_now() - timedelta(hours=1)
    job_id = str(uuid.uuid4())
    await app.state.repo.insert_job(
        JobRecord(id=job_id, status="processing", input_kind="url", input_url=RISKY_LINK, locale="en-US",
                  created_at=old, updated_at=old, attempts=settings.max_attempts)
    )
    await app.state.queue.put(job_id)
    data = await wait_for_job(client, job_id)
    assert data["status"] == "failed"
    assert data["error"]["code"] == "interrupted"
    assert data["verification"] is None


def test_status_invariants():
    base = dict(id=uuid.uuid4(), created_at=utc_now(), updated_at=utc_now(), preview=None,
                verification_id=None, verification=None, error=None)
    VerificationJob(status="pending", progress=0, **base)
    with pytest.raises(ValueError):
        VerificationJob(status="completed", progress=100, **base)
    with pytest.raises(ValueError):
        VerificationJob(status="processing", progress=100, **base)
    with pytest.raises(ValueError):
        VerificationJob(status="failed", progress=40, **base)
