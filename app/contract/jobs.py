"""Request and response models for /api/v1/verification-jobs."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, HttpUrl, model_validator

from .common import ContractModel, JobStatus
from .result import ProductPreview, VerificationResult

Locale = Literal["en-US", "id-ID"]


class CreateVerificationJobRequest(ContractModel):
    url: HttpUrl
    locale: Locale = "en-US"


class VerificationJobAccepted(ContractModel):
    id: UUID
    # New jobs are pending or processing. A replayed Idempotency-Key returns
    # the original job, which may already be completed or failed.
    status: JobStatus
    status_url: str
    created_at: datetime
    poll_after_seconds: int = Field(ge=1, le=30)


class CreateVerificationJobResponse(ContractModel):
    data: VerificationJobAccepted
    meta: dict[str, str]


class JobFailure(ContractModel):
    code: str
    message: str


class JobPreview(ContractModel):
    """Product shown in the "Is this the product?" step while analysis runs."""

    displayName: str
    product: ProductPreview


class VerificationJob(ContractModel):
    id: UUID
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    progress: int = Field(ge=0, le=100)
    preview: JobPreview | None
    verification_id: UUID | None
    verification: VerificationResult | None
    error: JobFailure | None

    @model_validator(mode="after")
    def validate_status_fields(self):
        done = self.status == JobStatus.completed
        failed = self.status == JobStatus.failed
        has_result = self.verification is not None and self.verification_id is not None
        if done and not (has_result and self.progress == 100 and self.error is None):
            raise ValueError("completed job needs verification, verification_id, progress 100 and no error")
        if failed and (self.error is None or self.verification is not None or self.verification_id is not None):
            raise ValueError("failed job needs error and no verification")
        if not done and not failed:
            if self.verification is not None or self.verification_id is not None or self.error is not None:
                raise ValueError("pending/processing job must not carry verification or error")
            if self.progress > 99:
                raise ValueError("pending/processing progress must be 0-99")
        if self.status == JobStatus.pending and self.preview is not None:
            raise ValueError("pending job has no preview yet")
        return self


class VerificationJobResponse(ContractModel):
    data: VerificationJob
    meta: dict[str, str]


class ApiErrorBody(ContractModel):
    code: str
    message: str
    request_id: str
