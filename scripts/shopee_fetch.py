"""Fetch one Shopee product through each configured provider and show the result.

    python -m scripts.shopee_fetch 'https://shopee.co.id/...-i.123.456'
    python -m scripts.shopee_fetch --save 'https://shopee.co.id/...-i.123.456'

Uses APIFY_TOKEN / RAPIDAPI_* from .env. Each provider is called separately, so
this costs one product (and up to SHOPEE_MAX_REVIEWS reviews) per provider.
--save writes the raw JSON responses to tests/fixtures/shopee/.
"""

import argparse
import asyncio
import json
import re

import httpx
from pydantic import HttpUrl, TypeAdapter, ValidationError

from app.config import BASE_DIR, load_settings
from app.sources.providers.base import ListingNotFound, ProviderError
from app.sources.shopee_url import UnsupportedLink, parse_shopee_link
from app.sources.short_links import resolve_short_link
from main import build_providers

FIXTURE_DIR = BASE_DIR / "tests" / "fixtures" / "shopee"


async def main(url: str, save: bool) -> None:
    settings = load_settings()
    try:
        link = parse_shopee_link(str(TypeAdapter(HttpUrl).validate_python(url)))
    except (ValidationError, UnsupportedLink):
        print(f"Not a Shopee product link: {url}\nTip: wrap the URL in single quotes.")
        return

    async def keep_raw(response: httpx.Response) -> None:
        if not save or "shp.ee" in response.url.host:
            return
        await response.aread()
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^\w.-]+", "_", f"{response.url.host}{response.url.path}")[-120:]
        path = FIXTURE_DIR / f"{link.item_id or 'short'}_{name}.json"
        path.write_text(response.text)
        print(f"  saved {path.relative_to(BASE_DIR)}")

    async with httpx.AsyncClient(timeout=settings.http_timeout_s, event_hooks={"response": [keep_raw]}) as http:
        if link.is_short:
            link = await resolve_short_link(link, http)
        providers = build_providers(settings, http)
        if not providers:
            print("No provider configured. Set APIFY_TOKEN and/or RAPIDAPI_KEY + RAPIDAPI_SHOPEE_DETAIL_URL in .env.")
            return
        for provider in providers:
            print(f"\n== {provider.name}")
            product, reviews = await asyncio.gather(
                provider.fetch_product(link),
                provider.fetch_reviews(link, settings.shopee_max_reviews),
                return_exceptions=True,
            )
            if isinstance(product, ListingNotFound):
                print("Product not found (removed or wrong link).")
                continue
            if isinstance(product, ProviderError):
                print(f"Product failed: {product}")
            elif isinstance(product, BaseException):
                raise product
            else:
                print(product.model_dump_json(indent=2, exclude={"reviews", "description", "image_urls"}))
                print(f"images: {len(product.image_urls)}, description: {len(product.description or '')} chars")
            if isinstance(reviews, BaseException):
                print(f"Reviews failed: {reviews}")
            else:
                stars = [r.stars for r in reviews]
                print(f"reviews: {len(reviews)} (stars {sorted(set(s for s in stars if s))})")
                for review in reviews[:3]:
                    print(f"  {review.stars}* {review.created_at} {review.text[:80]!r}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url")
    parser.add_argument("--save", action="store_true", help="save raw JSON responses as fixtures")
    args = parser.parse_args()
    asyncio.run(main(args.url, args.save))
