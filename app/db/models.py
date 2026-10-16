"""Storage-independent records shared by the API, the job runner and the sources."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class JobRecord:
    id: str
    status: str
    input_kind: str
    locale: str
    created_at: datetime
    updated_at: datetime
    progress: int = 0
    input_url: str | None = None
    canonical_url: str | None = None
    input: dict[str, Any] = field(default_factory=dict)
    preview: dict[str, Any] | None = None
    verification_id: str | None = None
    from_cache: bool = False
    error_code: str | None = None
    error_message: str | None = None
    attempts: int = 0
