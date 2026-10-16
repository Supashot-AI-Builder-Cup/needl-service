"""POST /api/v1/verification-jobs and GET /api/v1/verification-jobs/{job_id}."""

import hashlib
import json
import uuid
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.types import Message, Receive

from ..contract.jobs import (
    CreateVerificationJobRequest,
    CreateVerificationJobResponse,
    JobFailure,
    JobPreview,
    VerificationJob,
    VerificationJobAccepted,
    VerificationJobResponse,
)
from ..db.models import JobRecord, utc_now
from ..jobs.progress import poll_after_seconds
from ..sources.screenshot_files import (
    EXTRA_FIELD,
    EXTRA_NAMES_FIELD,
    SLOT_FIELDS,
    read_screenshot_form,
    remove_upload,
    save_upload,
)
from ..sources.shopee_url import UnsupportedLink, parse_shopee_link
from .errors import ApiError, request_id_of

router = APIRouter(prefix="/api/v1", tags=["verification-jobs"])

MAX_JSON_BYTES = 16 * 1024
# Multipart boundaries and headers add a little on top of the image bytes.
MULTIPART_OVERHEAD_BYTES = 256 * 1024
SCREENSHOT_KEYS = set(SLOT_FIELDS) | {EXTRA_FIELD, EXTRA_NAMES_FIELD}

_REQUEST_BODIES = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": CreateVerificationJobRequest.model_json_schema()},
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["product", "store"],
                    "properties": {
                        **{k: {"type": "string", "format": "binary"} for k in SLOT_FIELDS},
                        EXTRA_FIELD: {"type": "array", "items": {"type": "string", "format": "binary"}, "maxItems": 5},
                        EXTRA_NAMES_FIELD: {"type": "array", "items": {"type": "string", "maxLength": 100}},
                        "locale": {"type": "string", "enum": ["en-US", "id-ID"], "default": "en-US"},
                    },
                }
            },
        },
    }
}


def _limited_receive(receive: Receive, limit: int) -> Receive:
    """Stop reading the body once it passes `limit` bytes (covers requests without Content-Length)."""
    seen = 0

    async def wrapped() -> Message:
        nonlocal seen
        message = await receive()
        if message["type"] == "http.request":
            seen += len(message.get("body", b""))
            if seen > limit:
                raise ApiError(413, "payload_too_large", "Request body is too large.")
        return message

    return wrapped


def _check_content_length(request: Request, limit: int) -> None:
    raw = request.headers.get("content-length")
    if raw and raw.isdigit() and int(raw) > limit:
        raise ApiError(413, "payload_too_large", "Request body is too large.")


def _check_idempotency_key(key: str | None) -> None:
    if key is None:
        return
    if not 1 <= len(key) <= 128 or not all(32 <= ord(c) <= 126 for c in key):
        raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key must be 1-128 printable ASCII characters.")


def _hash(fingerprint: dict) -> str:
    return hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()


async def _read_link_request(request: Request) -> tuple[CreateVerificationJobRequest, str | None]:
    _check_content_length(request, MAX_JSON_BYTES)
    limited = Request(request.scope, _limited_receive(request.receive, MAX_JSON_BYTES))
    body = await limited.body()
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError(400, "invalid_json", "Request body is not valid JSON.")
    if isinstance(payload, dict) and SCREENSHOT_KEYS & payload.keys():
        raise ApiError(400, "mixed_input", "Send either a url (JSON) or screenshots (multipart), not both.")
    try:
        body_model = CreateVerificationJobRequest.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**err, "loc": ("body", *err["loc"])} for err in exc.errors(include_url=False)]
        )
    url = str(body_model.url)
    try:
        link = parse_shopee_link(url)
    except UnsupportedLink as exc:
        msg = (
            "URL host is not a supported marketplace"
            if exc.reason == "host"
            else "URL is not a Shopee product link"
        )
        raise RequestValidationError([{"loc": ("body", "url"), "msg": msg, "type": "value_error", "input": url}])
    return body_model, link.canonical_url


@router.post(
    "/verification-jobs",
    status_code=202,
    response_model=CreateVerificationJobResponse,
    openapi_extra=_REQUEST_BODIES,
    summary="Create a verification job from a Shopee link (JSON) or screenshots (multipart)",
)
async def create_verification_job(
    request: Request,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    state = request.app.state
    state.limiter.check(request, "create")
    _check_idempotency_key(idempotency_key)

    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    job_id = str(uuid.uuid4())
    now = utc_now()
    upload = None

    if content_type == "application/json":
        body, canonical = await _read_link_request(request)
        url = str(body.url)
        request_hash = _hash({"mode": "url", "url": canonical or url, "locale": body.locale})
        job = JobRecord(
            id=job_id,
            status="pending",
            input_kind="url",
            input_url=url,
            canonical_url=canonical,
            locale=body.locale,
            created_at=now,
            updated_at=now,
        )
    elif content_type == "multipart/form-data":
        max_total = state.settings.max_upload_total_bytes
        max_body = max_total + MULTIPART_OVERHEAD_BYTES
        _check_content_length(request, max_body)
        limited = Request(request.scope, _limited_receive(request.receive, max_body))
        form = await limited.form(max_files=20, max_fields=20)
        try:
            if "url" in form:
                raise ApiError(400, "mixed_input", "Send either a url (JSON) or screenshots (multipart), not both.")
            upload = await read_screenshot_form(form, max_total)
        finally:
            await form.close()
        request_hash = upload.request_hash()
        job = JobRecord(
            id=job_id,
            status="pending",
            input_kind="screenshots",
            locale=upload.locale,
            created_at=now,
            updated_at=now,
        )
    else:
        raise ApiError(
            415,
            "unsupported_media_type",
            "Use application/json for a link or multipart/form-data for screenshots.",
        )

    repo = state.repo
    if idempotency_key:
        replay = await _replay(request, idempotency_key, request_hash)
        if replay is not None:
            return replay

    if upload is not None:
        job.input = save_upload(upload, state.settings.upload_dir / job_id)
    await repo.insert_job(job)

    if idempotency_key and not await repo.insert_idempotency(idempotency_key, request_hash, job_id):
        # Another request with the same key won the race: drop ours and answer like a replay.
        await repo.delete_job(job_id)
        remove_upload(job.input.get("dir"))
        replay = await _replay(request, idempotency_key, request_hash)
        if replay is not None:
            return replay

    await state.queue.put(job_id)
    return _accepted(request, job, status_code=202)


async def _replay(request: Request, key: str, request_hash: str) -> JSONResponse | None:
    repo = request.app.state.repo
    stored = await repo.get_idempotency(key)
    if stored is None:
        return None
    stored_hash, stored_job_id, _ = stored
    if stored_hash != request_hash:
        raise ApiError(409, "idempotency_conflict", "Idempotency-Key was already used with a different request.")
    job = await repo.get_job(stored_job_id)
    if job is None:
        await repo.delete_idempotency(key)
        return None
    return _accepted(request, job, status_code=200)


def _accepted(request: Request, job: JobRecord, status_code: int) -> JSONResponse:
    status_url = f"/api/v1/verification-jobs/{job.id}"
    poll_after = poll_after_seconds(job.created_at, utc_now())
    body = CreateVerificationJobResponse(
        data=VerificationJobAccepted(
            id=job.id,
            status=job.status,
            status_url=status_url,
            created_at=job.created_at,
            poll_after_seconds=poll_after,
        ),
        meta={"request_id": request_id_of(request)},
    )
    headers = {"Location": status_url}
    if job.status in ("pending", "processing"):
        headers["Retry-After"] = str(poll_after)
    return JSONResponse(body.model_dump(mode="json"), status_code=status_code, headers=headers)


@router.get(
    "/verification-jobs/{job_id}",
    response_model=VerificationJobResponse,
    summary="Read job progress, preview and, once completed, the result",
)
async def get_verification_job(job_id: UUID, request: Request, response: Response):
    state = request.app.state
    state.limiter.check(request, "poll")
    job = await state.repo.get_job(str(job_id))
    if job is None:
        raise ApiError(404, "job_not_found", "Verification job was not found.")

    verification = None
    if job.status == "completed" and job.verification_id:
        verification = await state.repo.get_verification(job.verification_id)
        if verification is not None and job.from_cache:
            verification = verification.model_copy(update={"cached": True})

    data = VerificationJob(
        id=job.id,
        status=job.status,
        created_at=job.created_at,
        updated_at=job.updated_at,
        progress=job.progress,
        preview=JobPreview.model_validate(job.preview) if job.preview else None,
        verification_id=job.verification_id if job.status == "completed" else None,
        verification=verification,
        error=JobFailure(code=job.error_code, message=job.error_message) if job.status == "failed" else None,
    )

    response.headers["Cache-Control"] = "no-store"
    if job.status in ("pending", "processing"):
        response.headers["Retry-After"] = str(poll_after_seconds(job.created_at, utc_now()))
    return VerificationJobResponse(data=data, meta={"request_id": request_id_of(request)})
