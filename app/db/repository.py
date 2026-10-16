"""Firestore access for jobs, verifications and idempotency keys.

Collections:
- verification_jobs/{job_id}: one document per job.
- verifications/{verification_id}: one saved result; `result_json` holds the full
  VerificationResult as a JSON string (exempted from indexing in deploy/setup.sh).
- verification_urls/{sha256(canonical_url)}: points to the newest result for a product,
  so the cache lookup is a single get without a composite index.
- idempotency_keys/{sha256(key)}: `expires_at` carries a Firestore TTL policy.

Jobs and verifications have no TTL: an Idempotency-Key replay expects its job to exist.
No query needs a composite index, so the emulator (which ignores indexes) and
production behave the same.
"""

import asyncio
import hashlib
import inspect
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from google.api_core.exceptions import NotFound
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import firestore
from google.cloud.firestore import FieldFilter

from ..config import Settings
from ..contract.result import VerificationResult
from .models import JobRecord, utc_now

log = logging.getLogger("needl.db")

JOBS = "verification_jobs"
VERIFICATIONS = "verifications"
VERIFICATION_URLS = "verification_urls"
IDEMPOTENCY_KEYS = "idempotency_keys"

UNFINISHED = ("pending", "processing")
ClaimOutcome = Literal["claimed", "skip", "exhausted"]

# Columns update_job may change, with a converter for the stored value.
_UPDATABLE = {
    "status": None,
    "progress": int,
    "canonical_url": None,
    "verification_id": None,
    "from_cache": bool,
    "error_code": None,
    "error_message": None,
    "attempts": int,
    "preview": None,
}

CREDENTIALS_HELP = (
    "Firestore credentials not found. Set FIRESTORE_EMULATOR_HOST for local dev "
    "or run `gcloud auth application-default login`."
)


def _doc_id(value: str) -> str:
    """Document IDs cannot contain '/', so free-form values are hashed."""
    return hashlib.sha256(value.encode()).hexdigest()


def _dt(value: datetime) -> datetime:
    """Firestore returns DatetimeWithNanoseconds; keep a plain UTC datetime."""
    v = value.astimezone(timezone.utc)
    return datetime(v.year, v.month, v.day, v.hour, v.minute, v.second, v.microsecond, tzinfo=timezone.utc)


def _job_to_doc(job: JobRecord) -> dict[str, Any]:
    return {
        "status": job.status,
        "progress": job.progress,
        "input_kind": job.input_kind,
        "input_url": job.input_url,
        "canonical_url": job.canonical_url,
        "input": job.input,
        "locale": job.locale,
        "preview": job.preview,
        "verification_id": job.verification_id,
        "from_cache": job.from_cache,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "attempts": job.attempts,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _job_from_doc(job_id: str, data: dict[str, Any]) -> JobRecord:
    return JobRecord(
        id=job_id,
        status=data["status"],
        progress=data.get("progress", 0),
        input_kind=data["input_kind"],
        input_url=data.get("input_url"),
        canonical_url=data.get("canonical_url"),
        input=data.get("input") or {},
        locale=data["locale"],
        preview=data.get("preview"),
        verification_id=data.get("verification_id"),
        from_cache=bool(data.get("from_cache", False)),
        error_code=data.get("error_code"),
        error_message=data.get("error_message"),
        attempts=data.get("attempts", 0),
        created_at=_dt(data["created_at"]),
        updated_at=_dt(data["updated_at"]),
    )


class Repository:
    def __init__(self, client: firestore.AsyncClient, idempotency_ttl_s: int):
        self.client = client
        self.idempotency_ttl_s = idempotency_ttl_s

    @classmethod
    async def open(cls, settings: Settings) -> "Repository":
        """Connect with Application Default Credentials, or to FIRESTORE_EMULATOR_HOST when set."""
        try:
            client = firestore.AsyncClient(project=settings.firestore_project, database=settings.firestore_database)
        except (DefaultCredentialsError, OSError) as exc:
            raise RuntimeError(CREDENTIALS_HELP) from exc
        return cls(client, settings.idempotency_ttl_s)

    async def close(self) -> None:
        self.client.close()
        # AsyncClient.close() leaves the gRPC channel open; close it on this event loop.
        api = getattr(self.client, "_firestore_api_internal", None)
        if api is not None:
            result = api.transport.close()
            if inspect.isawaitable(result):
                await result

    def _job_ref(self, job_id: str):
        return self.client.collection(JOBS).document(job_id)

    def _idempotency_ref(self, key: str):
        return self.client.collection(IDEMPOTENCY_KEYS).document(_doc_id(key))

    # ---- jobs ----

    async def insert_job(self, job: JobRecord) -> None:
        await self._job_ref(job.id).create(_job_to_doc(job))

    async def get_job(self, job_id: str) -> JobRecord | None:
        snap = await self._job_ref(job_id).get()
        return _job_from_doc(snap.id, snap.to_dict()) if snap.exists else None

    async def update_job(self, job_id: str, **changes: Any) -> None:
        data: dict[str, Any] = {}
        for key, value in changes.items():
            if key not in _UPDATABLE:
                raise KeyError(f"unknown job column: {key}")
            convert = _UPDATABLE[key]
            data[key] = convert(value) if convert and value is not None else value
        data["updated_at"] = utc_now()
        try:
            await self._job_ref(job_id).update(data)
        except NotFound:
            log.warning("update of missing job %s ignored", job_id)

    async def delete_job(self, job_id: str) -> None:
        await self._job_ref(job_id).delete()

    async def unfinished_jobs(self) -> list[JobRecord]:
        # No order_by: filter + order on different fields would need a composite index.
        query = self.client.collection(JOBS).where(filter=FieldFilter("status", "in", list(UNFINISHED)))
        jobs = [_job_from_doc(snap.id, snap.to_dict()) async for snap in query.stream()]
        return sorted(jobs, key=lambda job: job.created_at)

    async def claim_job(
        self, job_id: str, stale_before: datetime, max_attempts: int, progress: int
    ) -> tuple[ClaimOutcome, JobRecord | None]:
        """Mark a job as processing by this worker, in a transaction.

        skip: unknown, finished, or processing and updated since `stale_before` (a live
        worker has it). exhausted: no attempts left (nothing written). claimed: the
        job is now processing with one more attempt.
        """
        ref = self._job_ref(job_id)

        @firestore.async_transactional
        async def claim(transaction) -> tuple[ClaimOutcome, JobRecord | None]:
            snap = await ref.get(transaction=transaction)
            if not snap.exists:
                return "skip", None
            job = _job_from_doc(snap.id, snap.to_dict())
            if job.status not in UNFINISHED:
                return "skip", None
            if job.status == "processing" and job.updated_at >= stale_before:
                return "skip", None
            if job.attempts >= max_attempts:
                return "exhausted", job
            now = utc_now()
            transaction.update(
                ref, {"status": "processing", "progress": progress, "attempts": job.attempts + 1, "updated_at": now}
            )
            job.status, job.progress, job.attempts, job.updated_at = "processing", progress, job.attempts + 1, now
            return "claimed", job

        return await self._transact(claim)

    async def _transact(self, fn):
        """Run an @async_transactional function in a new transaction.

        When the task is cancelled while the transaction begins (server shutdown), the
        library tries to roll back a transaction that never started and raises
        ValueError instead. Raise the cancellation again so shutdown does not hang.
        """
        try:
            return await fn(self.client.transaction())
        except Exception as exc:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise asyncio.CancelledError() from exc
            raise

    # ---- verifications ----

    async def insert_verification(self, result: VerificationResult) -> None:
        verification_id = str(result.id)
        canonical_url = str(result.source.canonical_url) if result.source.canonical_url else None
        batch = self.client.batch()
        batch.set(
            self.client.collection(VERIFICATIONS).document(verification_id),
            {
                "source_kind": result.source.kind,
                "canonical_url": canonical_url,
                "name": result.displayName,
                "price": result.product.price,
                "store_name": result.metadata.store_name,
                "trust_score": result.trust_score,
                "risk_level": result.risk_level.value,
                "finding_count": sum(1 for c in result.checks if c.severity != "pass"),
                "checked_at": result.checked_at,
                "result_json": result.model_dump_json(),
            },
        )
        if canonical_url:
            # Newest result per product. Concurrent writers: last one wins, both are fresh.
            batch.set(
                self.client.collection(VERIFICATION_URLS).document(_doc_id(canonical_url)),
                {"canonical_url": canonical_url, "verification_id": verification_id, "checked_at": result.checked_at},
            )
        await batch.commit()

    async def get_verification(self, verification_id: str) -> VerificationResult | None:
        snap = await self.client.collection(VERIFICATIONS).document(verification_id).get()
        return VerificationResult.model_validate_json(snap.get("result_json")) if snap.exists else None

    async def find_fresh_verification(self, canonical_url: str, newer_than: datetime) -> str | None:
        snap = await self.client.collection(VERIFICATION_URLS).document(_doc_id(canonical_url)).get()
        if not snap.exists or _dt(snap.get("checked_at")) <= newer_than:
            return None
        return snap.get("verification_id")

    # ---- idempotency ----

    async def get_idempotency(self, key: str) -> tuple[str, str, datetime] | None:
        snap = await self._idempotency_ref(key).get()
        if not snap.exists:
            return None
        data = snap.to_dict()
        # The TTL policy deletes expired documents later (often within a day), so check here.
        if data.get("expires_at") is not None and _dt(data["expires_at"]) <= utc_now():
            return None
        return data["request_hash"], data["job_id"], _dt(data["created_at"])

    async def insert_idempotency(self, key: str, request_hash: str, job_id: str) -> bool:
        """False when another request stored the same key first and it has not expired."""
        ref = self._idempotency_ref(key)

        @firestore.async_transactional
        async def insert(transaction) -> bool:
            now = utc_now()
            snap = await ref.get(transaction=transaction)
            if snap.exists:
                expires_at = snap.to_dict().get("expires_at")
                if expires_at is None or _dt(expires_at) > now:
                    return False
            transaction.set(
                ref,
                {
                    "key": key,
                    "request_hash": request_hash,
                    "job_id": job_id,
                    "created_at": now,
                    "expires_at": now + timedelta(seconds=self.idempotency_ttl_s),
                },
            )
            return True

        return await self._transact(insert)

    async def delete_idempotency(self, key: str) -> None:
        await self._idempotency_ref(key).delete()
