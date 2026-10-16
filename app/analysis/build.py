"""Assemble the VerificationResult from a listing and the analysis output."""

import uuid

from ..contract.result import ListingMetadata, ProductCard, SourceMetadata, VerificationResult
from ..db.repository import utc_now
from ..sources.base import RawListing
from .base import AnalysisOutput
from .present import build_preview, display_name, format_rupiah
from .scoring import risk_level, risk_score, ui_verdict


def build_result(listing: RawListing, analysis: AnalysisOutput) -> VerificationResult:
    score = risk_score(analysis.checks)
    level = risk_level(score)
    preview = build_preview(listing)

    if listing.source_kind == "url":
        source = SourceMetadata(
            kind="url",
            url=listing.requested_url,
            canonical_url=listing.canonical_url,
            marketplace="shopee",
            fetched_at=listing.fetched_at,
        )
    else:
        source = SourceMetadata(kind="screenshots", screenshot_count=listing.screenshot_count)

    return VerificationResult(
        id=uuid.uuid4(),
        source=source,
        metadata=ListingMetadata(
            product_name=listing.title,
            store_name=listing.shop_name,
            store_is_official=listing.is_mall,
            price=format_rupiah(listing.price) if listing.price is not None else None,
            currency=listing.currency,
            rating=listing.rating,
            review_count=listing.rating_count,
            sold_count=listing.sold_count,
        ),
        trust_score=100 - score,
        risk_level=level,
        displayName=display_name(listing),
        product=ProductCard(**preview.model_dump(), score=score, verdict=ui_verdict(level)),
        checks=analysis.checks,
        stores=analysis.stores,
        questions=analysis.questions,
        checked_at=utc_now(),
        cached=False,
    )
