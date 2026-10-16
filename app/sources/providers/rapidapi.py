"""Shopee data from an API on RapidAPI (https://rapidapi.com).

RapidAPI hosts several Shopee APIs with different paths and parameters, so the
endpoints come from configuration as URL templates, for example:

  RAPIDAPI_SHOPEE_DETAIL_URL=https://<host>/<path>?shop_id={shop_id}&item_id={item_id}

Placeholders: {shop_id}, {item_id}, {url} (URL-encoded product link), {limit}.
Responses are read by normalize.py, which understands Shopee's own JSON shape
(the one most of these APIs pass through) and camelCase records.
"""

from urllib.parse import quote, urlparse

import httpx

from ..base import RawListing, RawReview
from ..shopee_url import ShopeeLink
from .base import ProviderError
from .normalize import listing_from_product, reviews_from_payload


class RapidApiProvider:
    name = "rapidapi"

    def __init__(self, http: httpx.AsyncClient, key: str, detail_url: str, reviews_url: str | None = None):
        self.http = http
        self.key = key
        self.detail_url = detail_url
        self.reviews_url = reviews_url

    def _url(self, template: str, link: ShopeeLink, limit: int = 0) -> str:
        return template.format(
            shop_id=link.shop_id,
            item_id=link.item_id,
            url=quote(link.url, safe=""),
            limit=limit,
        )

    async def _get(self, url: str):
        headers = {"x-rapidapi-key": self.key, "x-rapidapi-host": urlparse(url).hostname or ""}
        try:
            response = await self.http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"rapidapi request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            # 401/403: key or subscription, 429: quota used up.
            raise ProviderError(f"rapidapi returned HTTP {response.status_code}: {response.text[:200]}")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("rapidapi returned invalid JSON") from exc

    async def fetch_product(self, link: ShopeeLink) -> RawListing:
        payload = await self._get(self._url(self.detail_url, link))
        if isinstance(payload, dict) and payload.get("error") not in (None, 0, False) and not payload.get("data"):
            raise ProviderError(f"rapidapi error payload: {str(payload)[:200]}")
        return listing_from_product(payload, link, requested_url=link.url)

    async def fetch_reviews(self, link: ShopeeLink, limit: int) -> list[RawReview]:
        if not self.reviews_url:
            return []
        payload = await self._get(self._url(self.reviews_url, link, limit))
        return reviews_from_payload(payload)[:limit]
