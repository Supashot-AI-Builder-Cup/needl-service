"""Link mode with NEEDL_SOURCE=live: ask the configured providers in order."""

import asyncio
import logging

import httpx

from ..db.repository import JobRecord
from .base import ProgressFn, RawListing, SourceError
from .providers.base import ListingNotFound, ProviderError, ShopeeProvider
from .shopee_url import parse_shopee_link
from .short_links import resolve_short_link

log = logging.getLogger("needl.providers")

UNAVAILABLE = "Listing data cannot be fetched right now. Try again later or use screenshots."


class ShopeeSource:
    def __init__(self, providers: list[ShopeeProvider], http: httpx.AsyncClient, max_reviews: int):
        self.providers = providers
        self.http = http
        self.max_reviews = max_reviews

    async def fetch(self, job: JobRecord, report: ProgressFn) -> RawListing:
        link = parse_shopee_link(job.input_url)
        if link.is_short:
            link = await resolve_short_link(link, self.http)
        await report(10)

        if not self.providers:
            log.error("No Shopee provider configured. Set APIFY_TOKEN and/or RAPIDAPI_* in .env.")
            raise SourceError("source_unavailable", UNAVAILABLE)

        for provider in self.providers:
            product, reviews = await asyncio.gather(
                provider.fetch_product(link),
                provider.fetch_reviews(link, self.max_reviews),
                return_exceptions=True,
            )
            if isinstance(product, ListingNotFound):
                raise SourceError("listing_not_found", "Product was not found on Shopee.")
            if isinstance(product, ProviderError):
                log.warning("provider %s failed for %s: %s", provider.name, link.canonical_url, product)
                continue
            if isinstance(product, BaseException):
                raise product
            if isinstance(reviews, BaseException):
                # Reviews are optional: the job goes on, checks that need them get less data.
                log.warning("provider %s reviews failed for %s: %s", provider.name, link.canonical_url, reviews)
                reviews = []
            product.requested_url = job.input_url
            product.reviews = reviews
            await report(25)
            return product

        raise SourceError("source_unavailable", UNAVAILABLE)
