"""Read saved verification history and results."""

import base64
import binascii
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Query, Request

from ..contract.common import RiskLevel
from ..contract.verifications import VerificationHistoryResponse, VerificationResponse
from ..db.repository import utc_now
from .errors import ApiError, request_id_of

router = APIRouter(prefix="/api/v1/verifications", tags=["verifications"])


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _encode_cursor(checked_at: str, verification_id: str) -> str:
    raw = json.dumps([checked_at, verification_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[str, str] | None:
    if cursor is None:
        return None
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        checked_at, verification_id = json.loads(raw)
        datetime.fromisoformat(checked_at)
        UUID(verification_id)
        if not isinstance(checked_at, str):
            raise ValueError
        return checked_at, verification_id
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError, binascii.Error):
        raise ApiError(400, "invalid_cursor", "Cursor is invalid.")


@router.get("", response_model=VerificationHistoryResponse)
async def list_verifications(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=500),
    risk_level: RiskLevel | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
):
    start, end = _timestamp(from_), _timestamp(to)
    if start is not None and end is not None and start >= end:
        raise ApiError(400, "invalid_date_range", "from must be earlier than to.")

    rows = await request.app.state.repo.list_verifications(
        limit=limit + 1,
        cursor=_decode_cursor(cursor),
        risk_level=risk_level.value if risk_level else None,
        from_=start,
        to=end,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for row in rows:
        risk = row["risk_level"]
        items.append(
            {
                "id": row["id"],
                "source_kind": row["source_kind"],
                "source_url": row["source_url"],
                "name": row["name"],
                "price": row["price"],
                "image": row["image"],
                "store_name": row["store_name"],
                "trust_score": row["trust_score"],
                "risk_level": risk,
                "score": 100 - row["trust_score"],
                "verdict": "safe" if risk == "safe" else "high",
                "finding_count": row["finding_count"],
                "checked_at": row["checked_at"],
            }
        )

    next_cursor = _encode_cursor(rows[-1]["checked_at"], rows[-1]["id"]) if has_more and rows else None
    return {
        "data": items,
        "meta": {"request_id": request_id_of(request), "next_cursor": next_cursor},
    }


@router.get("/{verification_id}", response_model=VerificationResponse)
async def get_verification(verification_id: UUID, request: Request):
    result = await request.app.state.repo.get_verification(str(verification_id))
    if result is None:
        raise ApiError(404, "verification_not_found", "Verification was not found.")
    return {"data": result, "meta": {"request_id": request_id_of(request)}}
