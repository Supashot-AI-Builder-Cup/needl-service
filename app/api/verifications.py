"""Read saved verification results."""

from uuid import UUID

from fastapi import APIRouter, Request

from ..contract.verifications import VerificationResponse
from .errors import ApiError, request_id_of

router = APIRouter(prefix="/api/v1/verifications", tags=["verifications"])


@router.get("/{verification_id}", response_model=VerificationResponse)
async def get_verification(verification_id: UUID, request: Request):
    result = await request.app.state.repo.get_verification(str(verification_id))
    if result is None:
        raise ApiError(404, "verification_not_found", "Verification was not found.")
    return {"data": result, "meta": {"request_id": request_id_of(request)}}
