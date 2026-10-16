"""What every source returns: one normalized listing, whatever the input was."""

from datetime import datetime
from typing import Awaitable, Callable, Literal, Protocol

from pydantic import BaseModel, Field

from ..db.repository import JobRecord

ProgressFn = Callable[[int], Awaitable[None]]


class RawReview(BaseModel):
    stars: int | None = None
    text: str = ""
    created_at: datetime | None = None
    photo_urls: list[str] = Field(default_factory=list)
    author_masked: str | None = None


class RawListing(BaseModel):
    source_kind: Literal["url", "screenshots"]
    requested_url: str | None = None
    canonical_url: str | None = None
    shop_id: str | None = None
    item_id: str | None = None
    title: str
    # Name to show in the UI; defaults to title.
    display_name: str | None = None
    price: int | None = None  # Rupiah
    price_was: int | None = None
    currency: str | None = "IDR"
    rating: float | None = None
    rating_count: int | None = None
    sold_count: int | None = None
    shop_name: str | None = None
    is_mall: bool | None = None
    shop_location: str | None = None
    shop_joined_at: datetime | None = None
    description: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    reviews: list[RawReview] = Field(default_factory=list)
    screenshot_count: int | None = None
    fetched_at: datetime | None = None
    # Only used by the stub analyzer to pick its sample result.
    stub_scenario: Literal["risky", "safe"] | None = None


class SourceError(Exception):
    """A failure the user can be told about. `message` must be safe to show."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Source(Protocol):
    async def fetch(self, job: JobRecord, report: ProgressFn) -> RawListing: ...
