"""Firestore repository against the emulator."""

import uuid
from datetime import timedelta

import pytest

from app.analysis.build import build_result
from app.analysis.stub import StubAnalyzer
from app.db.models import JobRecord, utc_now
from app.db.repository import IDEMPOTENCY_KEYS, VERIFICATION_URLS, Repository, _doc_id
from app.sources.stub import StubSource
from tests.conftest import RISKY_LINK, SAFE_LINK


async def noop(_: int) -> None:
    pass


@pytest.fixture
async def repo(settings):
    repository = await Repository.open(settings)
    yield repository
    await repository.close()


def make_job(**changes) -> JobRecord:
    now = utc_now()
    fields = dict(
        id=str(uuid.uuid4()),
        status="pending",
        input_kind="url",
        input_url=RISKY_LINK,
        canonical_url="https://shopee.co.id/product/773019284/25810442671",
        locale="id-ID",
        created_at=now,
        updated_at=now,
    )
    fields.update(changes)
    return JobRecord(**fields)


async def make_result(link: str = RISKY_LINK, kind: str = "url"):
    job = make_job(input_url=link, input_kind=kind)
    if kind == "screenshots":
        job.input = {"dir": "/tmp/x", "slots": {"product": "a.png", "store": "b.png"}, "extras": []}
    listing = await StubSource(0).fetch(job, noop)
    return build_result(listing, await StubAnalyzer(0).run(listing, noop))


# ---- jobs ----


async def test_job_round_trip(repo):
    job = make_job(
        status="processing",
        progress=30,
        input={"dir": "/tmp/u", "slots": {"product": "a.png"}, "extras": [{"name": "X", "file": "b.png"}]},
        preview=None,
        attempts=1,
    )
    await repo.insert_job(job)
    assert await repo.get_job(job.id) == job


async def test_unknown_job_is_none(repo):
    assert await repo.get_job(str(uuid.uuid4())) is None


async def test_update_job(repo):
    job = make_job(created_at=utc_now() - timedelta(minutes=5), updated_at=utc_now() - timedelta(minutes=5))
    await repo.insert_job(job)
    preview = {"displayName": "Serum", "product": {"name": "Serum", "sold": {"en": "1K", "id": "1RB"}}}
    await repo.update_job(job.id, status="processing", progress=30, preview=preview, from_cache=1)

    stored = await repo.get_job(job.id)
    assert stored.status == "processing" and stored.progress == 30
    assert stored.preview == preview
    assert stored.from_cache is True
    assert stored.updated_at > job.updated_at
    assert stored.created_at == job.created_at


async def test_update_job_rejects_unknown_columns(repo):
    job = make_job()
    await repo.insert_job(job)
    with pytest.raises(KeyError):
        await repo.update_job(job.id, input_kind="screenshots")


async def test_update_of_missing_job_is_ignored(repo):
    await repo.update_job(str(uuid.uuid4()), status="failed")


async def test_delete_job(repo):
    job = make_job()
    await repo.insert_job(job)
    await repo.delete_job(job.id)
    assert await repo.get_job(job.id) is None


async def test_unfinished_jobs_are_pending_or_processing_oldest_first(repo):
    now = utc_now()
    newer = make_job(status="pending", created_at=now, updated_at=now)
    older = make_job(status="processing", created_at=now - timedelta(minutes=1), updated_at=now)
    await repo.insert_job(newer)
    await repo.insert_job(older)
    await repo.insert_job(make_job(status="completed"))
    await repo.insert_job(make_job(status="failed"))
    assert [j.id for j in await repo.unfinished_jobs()] == [older.id, newer.id]


# ---- claim ----


async def test_claim_pending_job(repo):
    job = make_job(attempts=0)
    await repo.insert_job(job)
    outcome, claimed = await repo.claim_job(job.id, utc_now() - timedelta(minutes=4), 2, 5)
    assert outcome == "claimed"
    assert (claimed.status, claimed.progress, claimed.attempts) == ("processing", 5, 1)
    assert await repo.get_job(job.id) == claimed


async def test_claim_rules(repo):
    stale_before = utc_now() - timedelta(minutes=4)
    old = utc_now() - timedelta(hours=1)
    fresh = make_job(status="processing", attempts=1)
    stale = make_job(status="processing", attempts=1, updated_at=old)
    done = make_job(status="completed")
    exhausted = make_job(status="processing", attempts=2, updated_at=old)
    for job in (fresh, stale, done, exhausted):
        await repo.insert_job(job)

    assert await repo.claim_job(fresh.id, stale_before, 2, 5) == ("skip", None)
    assert await repo.claim_job(done.id, stale_before, 2, 5) == ("skip", None)
    assert await repo.claim_job(str(uuid.uuid4()), stale_before, 2, 5) == ("skip", None)
    outcome, job = await repo.claim_job(exhausted.id, stale_before, 2, 5)
    assert outcome == "exhausted" and job == exhausted
    assert (await repo.get_job(exhausted.id)) == exhausted  # nothing written
    outcome, job = await repo.claim_job(stale.id, stale_before, 2, 5)
    assert outcome == "claimed" and job.attempts == 2


async def test_second_claim_in_a_row_is_skipped(repo):
    job = make_job()
    await repo.insert_job(job)
    stale_before = utc_now() - timedelta(minutes=4)
    assert (await repo.claim_job(job.id, stale_before, 2, 5))[0] == "claimed"
    assert await repo.claim_job(job.id, stale_before, 2, 5) == ("skip", None)


# ---- verifications ----


async def test_verification_round_trip(repo):
    result = await make_result()
    await repo.insert_verification(result)
    assert await repo.get_verification(str(result.id)) == result
    assert await repo.get_verification(str(uuid.uuid4())) is None


async def test_find_fresh_verification(repo):
    result = await make_result()
    await repo.insert_verification(result)
    url = str(result.source.canonical_url)

    assert await repo.find_fresh_verification(url, utc_now() - timedelta(hours=1)) == str(result.id)
    assert await repo.find_fresh_verification(url, result.checked_at) is None
    assert await repo.find_fresh_verification("https://shopee.co.id/product/1/2", utc_now() - timedelta(hours=1)) is None


async def test_newest_verification_wins(repo):
    first = await make_result()
    second = await make_result()
    await repo.insert_verification(first)
    await repo.insert_verification(second)
    url = str(second.source.canonical_url)
    assert await repo.find_fresh_verification(url, utc_now() - timedelta(hours=1)) == str(second.id)


async def test_safe_and_risky_products_have_separate_pointers(repo):
    risky, safe = await make_result(RISKY_LINK), await make_result(SAFE_LINK)
    await repo.insert_verification(risky)
    await repo.insert_verification(safe)
    since = utc_now() - timedelta(hours=1)
    assert await repo.find_fresh_verification(str(risky.source.canonical_url), since) == str(risky.id)
    assert await repo.find_fresh_verification(str(safe.source.canonical_url), since) == str(safe.id)


async def test_screenshot_verification_has_no_url_pointer(repo):
    result = await make_result(kind="screenshots")
    await repo.insert_verification(result)
    assert await repo.get_verification(str(result.id)) == result
    pointers = [snap async for snap in repo.client.collection(VERIFICATION_URLS).stream()]
    assert pointers == []


# ---- idempotency ----


async def test_idempotency_insert_get_delete(repo):
    key = "scan/123?" + "x" * 119
    assert len(key) == 128
    assert await repo.insert_idempotency(key, "hash-a", "job-1") is True
    assert await repo.insert_idempotency(key, "hash-b", "job-2") is False

    request_hash, job_id, created_at = await repo.get_idempotency(key)
    assert (request_hash, job_id) == ("hash-a", "job-1")
    assert created_at <= utc_now()

    stored = (await repo.client.collection(IDEMPOTENCY_KEYS).document(_doc_id(key)).get()).to_dict()
    assert stored["key"] == key
    assert stored["expires_at"] > stored["created_at"]

    await repo.delete_idempotency(key)
    assert await repo.get_idempotency(key) is None


async def test_expired_idempotency_key_can_be_reused(settings):
    repo = await Repository.open(settings.with_overrides(idempotency_ttl_s=0))
    try:
        assert await repo.insert_idempotency("k", "hash-a", "job-1") is True
        assert await repo.get_idempotency("k") is None
        assert await repo.insert_idempotency("k", "hash-b", "job-2") is True
        stored = (await repo.client.collection(IDEMPOTENCY_KEYS).document(_doc_id("k")).get()).to_dict()
        assert (stored["request_hash"], stored["job_id"]) == ("hash-b", "job-2")
    finally:
        await repo.close()
