"""A Shopee data provider turns a product link into a listing and its reviews."""

from typing import Protocol

from ..base import RawListing, RawReview
from ..shopee_url import ShopeeLink


class ProviderError(Exception):
    """The provider could not answer; the next provider may still work."""


class ListingNotFound(Exception):
    """The provider answered, and the product does not exist or was removed."""


class ShopeeProvider(Protocol):
    name: str

    async def fetch_product(self, link: ShopeeLink) -> RawListing: ...

    async def fetch_reviews(self, link: ShopeeLink, limit: int) -> list[RawReview]: ...
