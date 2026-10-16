"""Shopee data from Apify actors (https://apify.com).

Product: zen-studio/shopee-product-detail-scraper
Reviews: zen-studio/shopee-product-reviews-scraper
Both run synchronously through `run-sync-get-dataset-items` and need no Shopee login.
"""

import logging

import httpx

from ..base import RawListing, RawReview
from ..shopee_url import ShopeeLink
from .base import ListingNotFound, ProviderError
from .normalize import listing_from_product, reviews_from_payload

log = logging.getLogger("needl.providers")

API_BASE = "https://api.apify.com/v2"


def actor_url(link: ShopeeLink) -> str:
    """The actors want a product URL with `-i.<shop>.<item>`; the slug itself is ignored by Shopee."""
    return f"https://shopee.co.id/product-i.{link.shop_id}.{link.item_id}"


class ApifyProvider:
    name = "apify"

    def __init__(
        self,
        http: httpx.AsyncClient,
        token: str,
        detail_actor: str,
        reviews_actor: str,
        timeout_s: int = 120,
        max_charge_usd: float | None = None,
    ):
        self.http = http
        self.token = token
        self.detail_actor = detail_actor
        self.reviews_actor = reviews_actor
        self.timeout_s = timeout_s
        self.max_charge_usd = max_charge_usd

    async def _run(self, actor: str, run_input: dict) -> list:
        params: dict[str, str | int | float] = {"timeout": self.timeout_s, "format": "json", "clean": 1}
        if self.max_charge_usd is not None:
            params["maxTotalChargeUsd"] = self.max_charge_usd
        try:
            response = await self.http.post(
                f"{API_BASE}/acts/{actor}/run-sync-get-dataset-items",
                params=params,
                json=run_input,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=self.timeout_s + 15,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"apify request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            # 401/403: token, 402: no credit, 408: run timed out, 400: run failed.
            raise ProviderError(f"apify {actor} returned HTTP {response.status_code}: {response.text[:200]}")
        try:
            items = response.json()
        except ValueError as exc:
            raise ProviderError("apify returned invalid JSON") from exc
        if not isinstance(items, list):
            raise ProviderError("apify returned an unexpected payload")
        return items

    async def fetch_product(self, link: ShopeeLink) -> RawListing:
        items = await self._run(self.detail_actor, {"startUrls": [{"url": actor_url(link)}], "includeReviews": False})
        products = [i for i in items if isinstance(i, dict) and str(i.get("itemId")) == link.item_id] or items
        if not products:
            raise ProviderError("apify returned no product")
        product = products[0]
        if product.get("listingStatus") not in (None, "available"):
            log.info("apify listingStatus=%s for item %s", product.get("listingStatus"), link.item_id)
            if not product.get("name"):
                raise ListingNotFound()
        return listing_from_product(product, link, requested_url=link.url)

    async def fetch_reviews(self, link: ShopeeLink, limit: int) -> list[RawReview]:
        items = await self._run(
            self.reviews_actor,
            {
                "startUrls": [{"url": actor_url(link)}],
                "maxReviewsPerProduct": limit,
                "starFilter": "all",
                "contentFilter": "all",
            },
        )
        return reviews_from_payload(items)[:limit]
