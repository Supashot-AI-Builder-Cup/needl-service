"""Shared building blocks of the API contract (API_Contract.md, v1)."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    # Reject unknown fields to catch client/server drift.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class JobStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class RiskLevel(StrEnum):
    safe = "safe"
    suspicious = "suspicious"
    fraud = "fraud"


class UiSeverity(StrEnum):
    critical = "critical"
    major = "major"
    minor = "minor"
    pass_ = "pass"


class SectionKey(StrEnum):
    store = "store"
    listing = "listing"
    reviews = "reviews"


class LocalizedText(ContractModel):
    en: str
    id: str


# Same as the UI's `string | { en, id }`.
LocalText = str | LocalizedText
