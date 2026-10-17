"""Contract for a saved verification detail response."""

from uuid import UUID

from .common import ContractModel
from .result import VerificationResult


class VerificationResponse(ContractModel):
    data: VerificationResult
    meta: dict[str, str]
