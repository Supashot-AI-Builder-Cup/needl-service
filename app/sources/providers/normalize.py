"""Map provider JSON to RawListing / RawReview.

Handles two shapes:
- camelCase records like the Apify Shopee actors return (`name`, `shopName`, `ratingCount`);
- Shopee's own web API (`data.item`, `item_rating`, prices multiplied by 100000),
  which many RapidAPI Shopee APIs pass through.
"""

from datetime import datetime, timezone
from typing import Any

from ...db.models import utc_now
from ..base import RawListing, RawReview
from ..parsing import native_price, parse_count, shopee_image_url
from ..shopee_url import ShopeeLink, canonical_url
from .base import ListingNotFound


def dig(obj: Any, path: str) -> Any:
    """dig(d, "item_rating.rating_count.0")"""
    for key in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list) and key.isdigit() and int(key) < len(obj):
            obj = obj[int(key)]
        else:
            return None
    return obj


def pick(obj: Any, *paths: str) -> Any:
    for path in paths:
        value = dig(obj, path)
        if value not in (None, "", [], {}):
            return value
    return None


def to_datetime(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        seconds = value / 1000 if value > 1e12 else value
        return datetime.fromtimestamp(seconds, timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        return parse_count(value)
    return None


def _image_urls(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    urls = []
    for item in value:
        raw = item if isinstance(item, str) else pick(item, "url", "image", "src")
        url = shopee_image_url(raw) if isinstance(raw, str) else None
        if url:
            urls.append(url)
    return urls


def _dedupe(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(urls))


def unwrap_product(payload: Any) -> tuple[dict, dict, bool]:
    """Return (item, shop, is_native). Raises ListingNotFound when there is no product."""
    root = payload
    if isinstance(root, list):
        root = root[0] if root else None
    if isinstance(root, dict) and isinstance(root.get("data"), dict):
        root = root["data"]
    if not isinstance(root, dict):
        raise ListingNotFound()
    item = root["item"] if isinstance(root.get("item"), dict) else root
    shop = pick(root, "shop_detailed", "shop_info", "shop") or {}
    native = any(k in item for k in ("itemid", "shopid", "item_rating"))
    return item, shop if isinstance(shop, dict) else {}, native


def listing_from_product(payload: Any, link: ShopeeLink, requested_url: str) -> RawListing:
    item, shop, native = unwrap_product(payload)
    title = pick(item, "name", "title")
    if not title:
        raise ListingNotFound()

    if native:
        price = native_price(pick(item, "price", "price_min"))
        was = native_price(pick(item, "price_before_discount", "price_min_before_discount"))
    else:
        price_value = _number(pick(item, "price", "priceMin"))
        was_value = _number(pick(item, "priceBeforeDiscount", "priceMinBeforeDiscount", "strikethroughPrice"))
        price = round(price_value) if price_value is not None else None
        was = round(was_value) if was_value is not None else None
    if was is not None and price is not None and was <= price:
        was = None

    shop_id = str(pick(item, "shopId", "shopid") or link.shop_id)
    item_id = str(pick(item, "itemId", "itemid") or link.item_id)
    rating = _number(pick(item, "rating", "item_rating.rating_star"))

    return RawListing(
        source_kind="url",
        requested_url=requested_url,
        canonical_url=canonical_url(shop_id, item_id),
        shop_id=shop_id,
        item_id=item_id,
        title=str(title),
        display_name=str(title),
        price=price,
        price_was=was,
        currency=pick(item, "currency") or "IDR",
        rating=rating if rating is not None and 0 <= rating <= 5 else None,
        rating_count=_count(pick(item, "ratingCount", "item_rating.rating_count.0", "cmt_count", "commentCount")),
        sold_count=_count(pick(item, "sold", "historicalSold", "historical_sold", "soldDisplayed")),
        shop_name=pick(item, "shopName", "shop_name") or pick(shop, "name"),
        is_mall=_official(item, shop),
        shop_location=pick(item, "shopLocation", "shop_location") or pick(shop, "shop_location"),
        shop_joined_at=to_datetime(pick(item, "shopCreatedAt") or pick(shop, "ctime")),
        description=pick(item, "description"),
        image_urls=_dedupe(_image_urls([pick(item, "image")]) + _image_urls(pick(item, "images"))),
        fetched_at=utc_now(),
    )


def _official(item: dict, shop: dict) -> bool | None:
    value = pick(item, "isOfficialShop", "is_official_shop")
    if value is None:
        value = pick(shop, "is_official_shop")
    return bool(value) if value is not None else None


def review_from_item(item: dict) -> RawReview | None:
    if not isinstance(item, dict):
        return None
    stars = _count(pick(item, "ratingStar", "rating_star", "rating"))
    text = pick(item, "comment", "text", "content") or ""
    author = None if item.get("isAnonymous") or item.get("anonymous") else pick(item, "author", "author_username")
    return RawReview(
        stars=stars if stars is not None and 1 <= stars <= 5 else None,
        text=str(text),
        created_at=to_datetime(pick(item, "createdAt", "ctime")),
        photo_urls=_image_urls(pick(item, "images")),
        author_masked=mask_name(author),
    )


def reviews_from_payload(payload: Any) -> list[RawReview]:
    """Accepts a list of reviews or Shopee's {"data": {"ratings": [...]}}."""
    items = payload if isinstance(payload, list) else pick(payload, "data.ratings", "ratings", "data.items", "items")
    if not isinstance(items, list):
        return []
    return [r for r in (review_from_item(i) for i in items) if r is not None]


def mask_name(name: Any) -> str | None:
    if not isinstance(name, str) or not name:
        return None
    if "*" in name:
        return name
    return name[0] + "*" * max(1, len(name) - 2) + (name[-1] if len(name) > 1 else "")
