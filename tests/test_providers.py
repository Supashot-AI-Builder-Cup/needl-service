"""Shopee providers (Apify, RapidAPI) with outgoing HTTP replaced by httpx.MockTransport."""

import json

import httpx
import pytest

from app.sources.providers.apify import ApifyProvider
from app.sources.providers.normalize import listing_from_product
from app.sources.shopee_url import parse_shopee_link
from main import create_app
from tests.conftest import JOBS, wait_for_job

LINK = "https://shopee.co.id/SKINTIFIC-Niacinamide-Set-i.380266264.18705675288?extraParams=x"
DETAIL_PATH = "/v2/acts/zen-studio~shopee-product-detail-scraper/run-sync-get-dataset-items"
REVIEWS_PATH = "/v2/acts/zen-studio~shopee-product-reviews-scraper/run-sync-get-dataset-items"
RAPID_URL = "https://shopee-test.p.rapidapi.com/item?shop_id={shop_id}&item_id={item_id}"

APIFY_PRODUCT = {
    "itemId": 18705675288,
    "shopId": 380266264,
    "url": "https://shopee.co.id/product-i.380266264.18705675288",
    "name": "SKINTIFIC Niacinamide Brightening Series 5 in 1",
    "price": 129000,
    "priceBeforeDiscount": 259000,
    "currency": "IDR",
    "sold": 52300,
    "rating": 4.91,
    "ratingCount": 18450,
    "shopName": "SKINTIFIC Official Shop",
    "shopLocation": "Kota Jakarta Barat",
    "isOfficialShop": True,
    "shopCreatedAt": "2021-03-02T08:00:00Z",
    "description": "Paket lengkap 5 in 1. BPOM NA18221204112.",
    "image": "https://down-id.img.susercontent.com/file/main",
    "images": ["https://down-id.img.susercontent.com/file/main", "https://down-id.img.susercontent.com/file/abc", "def"],
    "listingStatus": "available",
}
APIFY_REVIEWS = [
    {"ratingStar": 5, "comment": "Bagus, asli", "createdAt": "2026-10-01T10:00:00Z", "author": "kristian", "images": ["h1"]},
    {"ratingStar": 1, "comment": "Bikin perih", "createdAt": 1759312800, "author": "x", "isAnonymous": True},
]
NATIVE_PAYLOAD = {
    "error": 0,
    "data": {
        "item": {
            "itemid": 18705675288,
            "shopid": 380266264,
            "title": "SKINTIFIC Niacinamide (native)",
            "price": 12_900_000_000,
            "price_before_discount": 25_900_000_000,
            "historical_sold": 52300,
            "item_rating": {"rating_star": 4.9, "rating_count": [18450, 100, 50, 300, 1000, 17000]},
            "shop_location": "Kota Jakarta Barat",
            "image": "nativehash",
            "images": ["nativehash", "second"],
        },
        "shop_detailed": {"name": "SKINTIFIC Official Shop", "is_official_shop": True, "ctime": 1614672000},
    },
}


class FakeUpstream:
    """Routes outgoing requests; each route returns (status, json) or an httpx.Response."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        key = f"{request.url.host}{request.url.path}"
        route = self.routes.get(key)
        if route is None:
            return httpx.Response(404, json={"error": f"no route for {key}"})
        if isinstance(route, httpx.Response):
            return route
        status, body = route
        return httpx.Response(status, json=body)


def live_settings(settings, **changes):
    return settings.with_overrides(
        source="live",
        apify_token="test-token",
        rapidapi_key="rapid-key",
        rapidapi_detail_url=RAPID_URL,
        shopee_providers=["apify", "rapidapi"],
        **changes,
    )


@pytest.fixture
def upstream():
    return FakeUpstream(
        {
            f"api.apify.com{DETAIL_PATH}": (201, [APIFY_PRODUCT]),
            f"api.apify.com{REVIEWS_PATH}": (201, APIFY_REVIEWS),
            "shopee-test.p.rapidapi.com/item": (200, NATIVE_PAYLOAD),
        }
    )


async def run_job(settings, upstream, url=LINK):
    app = create_app(live_settings(settings), http_transport=httpx.MockTransport(upstream))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(JOBS, json={"url": url})
            assert res.status_code == 202, res.text
            return await wait_for_job(client, res.json()["data"]["id"])


async def test_apify_product_becomes_preview_and_result(settings, upstream):
    data = await run_job(settings, upstream)
    assert data["status"] == "completed"
    product = data["preview"]["product"]
    assert product == {
        "name": "SKINTIFIC Niacinamide Brightening Series 5 in 1",
        "price": "Rp129.000",
        "was": "Rp259.000",
        "rating": "4.9",
        "ratings": "18,4RB",
        "sold": {"en": "52.3K", "id": "52,3RB"},
        "store": "SKINTIFIC Official Shop",
        "mall": True,
        "loc": "Kota Jakarta Barat",
        "image": "https://down-id.img.susercontent.com/file/main",
    }
    result = data["verification"]
    assert result["product"]["image"] == "https://down-id.img.susercontent.com/file/main"
    assert result["source"]["canonical_url"] == "https://shopee.co.id/product/380266264/18705675288"
    assert result["source"]["url"] == LINK
    assert result["metadata"]["store_is_official"] is True

    detail_call = next(c for c in upstream.calls if c.url.path == DETAIL_PATH)
    assert detail_call.headers["authorization"] == "Bearer test-token"
    assert json.loads(detail_call.content) == {
        "startUrls": [{"url": "https://shopee.co.id/product-i.380266264.18705675288"}],
        "includeReviews": False,
    }
    reviews_call = next(c for c in upstream.calls if c.url.path == REVIEWS_PATH)
    assert json.loads(reviews_call.content)["maxReviewsPerProduct"] == 100
    assert not any(c.url.host.endswith("rapidapi.com") for c in upstream.calls)


async def test_failed_reviews_do_not_fail_the_job(settings, upstream):
    upstream.routes[f"api.apify.com{REVIEWS_PATH}"] = (400, {"error": {"type": "run-failed"}})
    data = await run_job(settings, upstream)
    assert data["status"] == "completed"


async def test_rapidapi_is_used_when_apify_fails(settings, upstream):
    upstream.routes[f"api.apify.com{DETAIL_PATH}"] = (402, {"error": {"type": "not-enough-usage"}})
    data = await run_job(settings, upstream)
    assert data["status"] == "completed"
    product = data["preview"]["product"]
    assert product["name"] == "SKINTIFIC Niacinamide (native)"
    assert product["price"] == "Rp129.000"  # 12_900_000_000 / 100000
    assert product["was"] == "Rp259.000"
    assert product["mall"] is True
    rapid_call = next(c for c in upstream.calls if c.url.host == "shopee-test.p.rapidapi.com")
    assert rapid_call.headers["x-rapidapi-key"] == "rapid-key"
    assert rapid_call.headers["x-rapidapi-host"] == "shopee-test.p.rapidapi.com"
    assert rapid_call.url.params["item_id"] == "18705675288"


async def test_all_providers_failing_is_source_unavailable(settings, upstream):
    upstream.routes[f"api.apify.com{DETAIL_PATH}"] = (500, {})
    upstream.routes["shopee-test.p.rapidapi.com/item"] = (429, {"message": "quota"})
    data = await run_job(settings, upstream)
    assert data["status"] == "failed"
    assert data["error"]["code"] == "source_unavailable"


async def test_removed_listing_is_listing_not_found(settings, upstream):
    upstream.routes[f"api.apify.com{DETAIL_PATH}"] = (201, [{"itemId": 18705675288, "listingStatus": "deleted"}])
    data = await run_job(settings, upstream)
    assert data["status"] == "failed"
    assert data["error"]["code"] == "listing_not_found"


async def test_short_link_is_resolved_before_calling_providers(settings, upstream):
    upstream.routes["id.shp.ee/AbC123"] = httpx.Response(302, headers={"location": LINK})
    data = await run_job(settings, upstream, url="https://id.shp.ee/AbC123")
    assert data["status"] == "completed"
    assert data["verification"]["source"]["canonical_url"] == "https://shopee.co.id/product/380266264/18705675288"


async def test_short_link_redirecting_off_shopee_is_refused(settings, upstream):
    upstream.routes["id.shp.ee/evil"] = httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
    data = await run_job(settings, upstream, url="https://id.shp.ee/evil")
    assert data["status"] == "failed"
    assert data["error"]["code"] == "unsupported_url"
    assert not any(c.url.host == "169.254.169.254" for c in upstream.calls)


async def test_apify_reviews_are_mapped(upstream):
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        provider = ApifyProvider(http, "t", "zen-studio~shopee-product-detail-scraper", "zen-studio~shopee-product-reviews-scraper")
        reviews = await provider.fetch_reviews(parse_shopee_link(LINK), limit=10)
    assert [(r.stars, r.text) for r in reviews] == [(5, "Bagus, asli"), (1, "Bikin perih")]
    assert reviews[0].author_masked == "k******n"
    assert reviews[1].author_masked is None  # anonymous
    assert reviews[0].photo_urls == ["https://down-id.img.susercontent.com/file/h1"]
    assert reviews[0].created_at.year == 2026 and reviews[1].created_at is not None


def test_native_shape_maps_rating_and_shop_age():
    listing = listing_from_product(NATIVE_PAYLOAD, parse_shopee_link(LINK), requested_url=LINK)
    assert listing.rating == 4.9
    assert listing.rating_count == 18450
    assert listing.sold_count == 52300
    assert listing.shop_joined_at.year == 2021
    assert listing.image_urls == [
        "https://down-id.img.susercontent.com/file/nativehash",
        "https://down-id.img.susercontent.com/file/second",
    ]
