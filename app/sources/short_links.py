"""Follow Shopee short links (shp.ee) to the product URL, only through allowed hosts."""

from urllib.parse import urljoin, urlparse

import httpx

from .base import SourceError
from .shopee_url import ShopeeLink, UnsupportedLink, is_allowed_host, parse_shopee_link

MAX_HOPS = 5
NOT_A_PRODUCT = "This link is not a Shopee product page."


async def resolve_short_link(link: ShopeeLink, http: httpx.AsyncClient) -> ShopeeLink:
    url = link.url
    for _ in range(MAX_HOPS):
        try:
            response = await http.get(url, follow_redirects=False)
        except httpx.HTTPError:
            raise SourceError("source_unavailable", "The short link could not be opened. Try the full product link.")
        location = response.headers.get("location")
        if response.status_code not in (301, 302, 303, 307, 308) or not location:
            break
        url = urljoin(url, location)
        # SSRF guard: never follow a redirect off Shopee's own hosts.
        if not is_allowed_host(urlparse(url).hostname or ""):
            raise SourceError("unsupported_url", NOT_A_PRODUCT)
        try:
            resolved = parse_shopee_link(url)
        except UnsupportedLink:
            continue
        if not resolved.is_short:
            return resolved
    raise SourceError("unsupported_url", NOT_A_PRODUCT)
