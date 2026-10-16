"""Screenshot mode: Gemini Vision reads the uploaded screenshots into a RawListing."""

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from ..db.models import JobRecord, utc_now
from ..llm import gemini
from .base import ProgressFn, RawListing, RawReview, SourceError
from .parsing import parse_count, parse_rupiah
from .screenshot_files import MIME

log = logging.getLogger("needl.sources")

UNAVAILABLE = "Screenshots cannot be read right now. Try again later."

SLOT_HINTS = {
    "product": "product page: main photo, product name, price",
    "store": "store header: store name, badge (Mall/Star), rating, followers, location",
    "description": "full product description, may include BPOM number and claims",
    "low_reviews": "1 to 3 star reviews",
    "newest_reviews": "reviews sorted by newest, with dates",
}

PROMPT = """You read screenshots of ONE Shopee Indonesia product listing.
Copy values exactly as shown. Use null when a value is not visible. Do not guess.
- price and price_before_discount: as printed, e.g. "Rp39.000".
- rating_count and sold: as printed, e.g. "1,2RB", "10RB+".
- is_mall: true only if a "Mall" or "Official Shop" badge is visible on the store.
- description: the full description text if a description screenshot is given.
- reviews: every review you can read, with star count, text and date as printed.
The labels before each image tell you which part of the listing it shows."""


class ExtractedReview(BaseModel):
    stars: int | None = Field(default=None, ge=1, le=5)
    text: str = ""
    date: str | None = None
    has_photo: bool = False


class ScreenshotExtraction(BaseModel):
    product_name: str | None = None
    price: str | None = None
    price_before_discount: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    rating_count: str | None = None
    sold: str | None = None
    store_name: str | None = None
    is_mall: bool | None = None
    store_location: str | None = None
    description: str | None = None
    reviews: list[ExtractedReview] = Field(default_factory=list)


class ScreenshotSource:
    async def fetch(self, job: JobRecord, report: ProgressFn) -> RawListing:
        manifest = job.input
        folder = Path(manifest["dir"])
        contents: list = [PROMPT]

        def add(label: str, filename: str) -> None:
            ext = filename.rsplit(".", 1)[-1]
            contents.append(label)
            contents.append(gemini.image_part((folder / filename).read_bytes(), MIME[ext]))

        for field, filename in manifest["slots"].items():
            add(f"Screenshot '{field}' ({SLOT_HINTS[field]}):", filename)
        for extra in manifest["extras"]:
            add(f"Extra screenshot named {extra['name']!r}:", extra["file"])

        await report(10)
        # service_busy: the problem is on our side (not configured, API or network error).
        # extraction_failed: Gemini answered, but not with readable listing data.
        try:
            data = await gemini.generate_structured(contents, ScreenshotExtraction)
        except RuntimeError:
            log.error("screenshot job %s: Gemini is not configured (GEMINI_API_KEY)", job.id)
            raise SourceError("service_busy", UNAVAILABLE)
        except ValueError:
            # Includes pydantic.ValidationError: the answer did not match the schema.
            log.warning("screenshot job %s: Gemini answer did not match the schema", job.id, exc_info=True)
            raise SourceError("extraction_failed", "Screenshots cannot be read. Make sure product and store names are visible.")
        except Exception:
            log.exception("screenshot job %s: Gemini request failed", job.id)
            raise SourceError("service_busy", UNAVAILABLE)
        await report(25)

        if not data.product_name:
            raise SourceError("extraction_failed", "No product name found. Make sure the product page screenshot is clear.")

        return RawListing(
            source_kind="screenshots",
            title=data.product_name,
            price=parse_rupiah(data.price),
            price_was=parse_rupiah(data.price_before_discount),
            rating=data.rating,
            rating_count=parse_count(data.rating_count),
            sold_count=parse_count(data.sold),
            shop_name=data.store_name,
            is_mall=data.is_mall,
            shop_location=data.store_location,
            description=data.description,
            reviews=[RawReview(stars=r.stars, text=r.text) for r in data.reviews],
            screenshot_count=len(manifest["slots"]) + len(manifest["extras"]),
            fetched_at=utc_now(),
        )
