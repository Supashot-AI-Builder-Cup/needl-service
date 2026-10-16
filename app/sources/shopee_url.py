"""Recognize Shopee product links and turn them into one canonical URL."""

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

SHOPEE_HOSTS = {"shopee.co.id", "www.shopee.co.id", "m.shopee.co.id"}
SHORT_LINK_DOMAIN = "shp.ee"
# Hosts a short link may redirect through (SSRF guard). Matched by suffix.
ALLOWED_HOST_SUFFIXES = (
    "shopee.co.id",
    "shp.ee",
    "susercontent.com",
    "shopeemobile.com",
    "shopee.com",
)

# Product ids at the end of the slug; anything after the ids (e.g. a stray "\") is ignored.
_SLUG_IDS = re.compile(r"-i\.(\d+)\.(\d+)(?!\d)")
_PRODUCT_PATH = re.compile(r"^/product/(\d+)/(\d+)/?$")


class UnsupportedLink(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ShopeeLink:
    url: str
    shop_id: str | None
    item_id: str | None
    is_short: bool
    slug_name: str | None

    @property
    def canonical_url(self) -> str | None:
        if self.shop_id and self.item_id:
            return canonical_url(self.shop_id, self.item_id)
        return None


def canonical_url(shop_id: str, item_id: str) -> str:
    return f"https://shopee.co.id/product/{shop_id}/{item_id}"


def is_short_host(host: str) -> bool:
    return host == SHORT_LINK_DOMAIN or host.endswith("." + SHORT_LINK_DOMAIN)


def is_allowed_host(host: str) -> bool:
    host = host.lower()
    return any(host == s or host.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES)


def parse_shopee_link(url: str) -> ShopeeLink:
    """Raise UnsupportedLink("host") or UnsupportedLink("path") for anything else."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https"):
        raise UnsupportedLink("host")
    if is_short_host(host):
        if not parsed.path.strip("/"):
            raise UnsupportedLink("path")
        return ShopeeLink(url=url, shop_id=None, item_id=None, is_short=True, slug_name=None)
    if host not in SHOPEE_HOSTS:
        raise UnsupportedLink("host")
    ids = ids_from_path(parsed.path)
    if ids is None:
        raise UnsupportedLink("path")
    return ShopeeLink(url=url, shop_id=ids[0], item_id=ids[1], is_short=False, slug_name=_slug_name(parsed.path))


def ids_from_path(path: str) -> tuple[str, str] | None:
    match = _PRODUCT_PATH.match(path)
    if match:
        return match.group(1), match.group(2)
    last = path.rstrip("/").rsplit("/", 1)[-1]
    match = _SLUG_IDS.search(unquote(last))
    return (match.group(1), match.group(2)) if match else None


def _slug_name(path: str) -> str | None:
    """"Brand-Serum-30ml-i.1.2" -> "Brand Serum 30ml" (same rule as the UI)."""
    segment = unquote(path.strip("/").split("/")[0])
    match = _SLUG_IDS.search(segment)
    slug = segment[: match.start()] if match else ""
    if len(slug) <= 6:
        return None
    return re.sub(r"\s+", " ", re.sub(r"-+", " ", slug)).strip()
