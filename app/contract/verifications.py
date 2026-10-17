"""Contracts for saved verification history and detail endpoints."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, HttpUrl

from .common import ContractModel, RiskLevel
from .result import VerificationResult


class VerificationHistoryItem(ContractModel):
    id: UUID
    source_kind: Literal["url", "screenshots"]
    source_url: HttpUrl | None
    name: str
    price: str
    # Same as the result's product.image: main product photo, null when unknown.
    image: str | None = None
    store_name: str | None
    trust_score: int = Field(ge=0, le=100)
    risk_level: RiskLevel
    score: int = Field(ge=0, le=100)
    verdict: Literal["high", "safe"]
    finding_count: int = Field(ge=0)
    checked_at: datetime


class VerificationHistoryResponse(ContractModel):
    data: list[VerificationHistoryItem]
    meta: dict[str, str | None]


class VerificationResponse(ContractModel):
    data: VerificationResult
    meta: dict[str, str]
