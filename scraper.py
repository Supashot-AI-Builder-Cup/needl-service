import asyncio
import ipaddress
import random
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

try:
    from playwright_stealth import Stealth

    _STEALTH_MODE = "v2"
except Exception:
    try:
        from playwright_stealth import stealth_async

        _STEALTH_MODE = "v1"
    except Exception:
        Stealth = None
        stealth_async = None
        _STEALTH_MODE = None

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
BLOCKED_RESOURCE_TYPES = {"stylesheet", "font", "media", "beacon"}
_SEM = asyncio.Semaphore(4)

EXTRACT_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const out = { meta: {}, jsonld: [], dom: {}, images: [], reviews: [] };
  for (const m of document.querySelectorAll('meta')) {
    const k = m.getAttribute('property') || m.getAttribute('name') || m.getAttribute('itemprop');
    const v = m.getAttribute('content');
    if (k && v && out.meta[k] === undefined) out.meta[k] = v;
  }
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try { out.jsonld.push(JSON.parse(s.textContent)); } catch (e) {}
  }
  const pick = (sels, max) => {
    for (const sel of sels) {
      let el = null;
      try { el = document.querySelector(sel); } catch (e) { continue; }
      if (!el) continue;
      const t = clean(el.getAttribute('content') || el.textContent);
      if (!t || (max && t.length > max)) continue;
      return t;
    }
    return null;
  };
  out.dom.title = pick([
    '[data-testid="lblProductName"]', '.pdp-mod-product-badge-title',
    'h1[class*="product"]', 'h1[class*="title"]', '[class*="product-title"]', 'h1'
  ], 300);
  out.dom.shop = pick([
    '[data-testid="lblShopName"]', '[data-testid="shopName"]', '.shop-name',
    '[class*="shop-name"]', '[class*="shopName"]', '[class*="seller-name"]',
    '.pdp-mod-store-name', '[class*="store-name"]'
  ], 120);
  out.dom.sold = pick([
    '[data-testid="lblTerjual"]', '[class*="sold"]', '[class*="terjual"]', '[class*="Sold"]'
  ], 60);
  out.dom.rating = pick([
    '[itemprop="ratingValue"]', '[data-testid="lblRating"]', '[class*="ratingValue"]',
    '[class*="rating"] [class*="value"]', '[class*="rating"]'
  ], 30);
  out.dom.price = pick([
    '[itemprop="price"]', '[data-testid*="price"]',
    '[class*="product-price"]', '[class*="price"], [class*="Price"]'
  ], 60);
  const seen = new Set();
  for (const im of document.querySelectorAll('img')) {
    let s = im.currentSrc || im.src || im.getAttribute('data-src') || im.getAttribute('data-lazy-src') || '';
    if (!s) continue;
    try { s = new URL(s, location.href).href; } catch (e) { continue; }
    if (!/^https?:/i.test(s) || s.length < 12 || seen.has(s)) continue;
    seen.add(s);
    out.images.push(s);
  }
  const reviewNodes = document.querySelectorAll('[data-testid*="review"] [class*="content"], [class*="review-item"], [class*="reviewItem"], [class*="comment"]');
  for (const n of reviewNodes) {
    const t = clean(n.textContent);
    if (t && t.length > 4 && out.reviews.length < 50) out.reviews.push(t.slice(0, 1000));
  }
  out.text = (document.body && document.body.innerText ? document.body.innerText : '')
    .replace(/\n{3,}/g, '\n\n').trim().slice(0, 20000);
  return out;
}
"""


def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    except ValueError:
        pass
    return True


async def _block_resources(route) -> None:
    try:
        if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
            await route.abort()
        else:
            await route.continue_()
    except Exception:
        try:
            await route.continue_()
        except Exception:
            pass


async def _autoscroll(page, steps: int = 6) -> None:
    for _ in range(steps):
        try:
            await page.mouse.wheel(0, random.randint(600, 1400))
        except Exception:
            pass
        await asyncio.sleep(random.uniform(0.4, 1.1))
    try:
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass


def _iter_nodes(node):
    if isinstance(node, list):
        for item in node:
            yield from _iter_nodes(item)
    elif isinstance(node, dict):
        if "@graph" in node:
            yield from _iter_nodes(node["@graph"])
        yield node


def _ld_nodes(raw: dict) -> list:
    nodes = []
    for block in raw.get("jsonld", []) or []:
        nodes.extend(_iter_nodes(block))
    return nodes


def _type_is(node: dict, name: str) -> bool:
    if not isinstance(node, dict):
        return False
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.lower() == name for x in types)


def _ld_product(nodes: list):
    for node in nodes:
        if _type_is(node, "product"):
            return node
    return None


def _first_str(value):
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _first_str(value.get("name") or value.get("url") or value.get("contentUrl"))
    if isinstance(value, list):
        for item in value:
            result = _first_str(item)
            if result:
                return result
    return None


def _to_float(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    text = re.sub(r"[^\d.,]", "", str(value))
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(value):
    number = _to_float(value)
    return int(number) if number is not None else None


_CURRENCY_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR", "¥": "JPY", "₹": "INR", "₩": "KRW"}


def _detect_currency(price, explicit):
    if explicit:
        return explicit
    if not price:
        return None
    text = str(price)
    if "Rp" in text:
        return "IDR"
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code
    return None


def _as_url_list(value) -> list:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _parse_product(raw: dict) -> dict:
    nodes = _ld_nodes(raw)
    product = _ld_product(nodes) or {}
    offers = product.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        offers = {}
    agg = product.get("aggregateRating") if isinstance(product.get("aggregateRating"), dict) else {}
    return {
        "name": _first_str(product.get("name")),
        "seller": _first_str(offers.get("seller")),
        "brand": _first_str(product.get("brand")),
        "price": offers.get("price") or offers.get("lowPrice") or offers.get("highPrice"),
        "currency": _first_str(offers.get("priceCurrency")),
        "rating": _to_float(agg.get("ratingValue")),
        "review_count": _to_int(agg.get("reviewCount") or agg.get("ratingCount")),
        "images": _as_url_list(product.get("image")),
    }


_REVIEW_BLOCKLIST = (
    "add to basket", "add to cart", "required", "be the first to review", "your email",
    "your rating", "your review", "cancel reply", "not be published", "save my name",
    "recent comments", "of 5 stars", "perfect good average", "quantity", "sku:",
)


def _looks_like_review(text) -> bool:
    if not isinstance(text, str):
        return False
    value = text.strip().lower()
    if not 20 <= len(value) <= 800:
        return False
    return not any(token in value for token in _REVIEW_BLOCKLIST)


def _one_review(node: dict):
    if not isinstance(node, dict):
        return None
    text = _first_str(node.get("reviewBody")) or _first_str(node.get("description")) or _first_str(node.get("name"))
    if not text:
        return None
    rating = None
    review_rating = node.get("reviewRating")
    if isinstance(review_rating, dict):
        rating = _to_float(review_rating.get("ratingValue"))
    return {
        "author": _first_str(node.get("author")),
        "rating": rating,
        "date": _first_str(node.get("datePublished")),
        "text": text[:1000],
    }


def _parse_reviews(raw: dict) -> list:
    raw_reviews = []
    nodes = _ld_nodes(raw)
    for node in nodes:
        if _type_is(node, "review"):
            raw_reviews.append(node)
    product = _ld_product(nodes)
    for node in (product or {}).get("review") or []:
        raw_reviews.append(node)
    parsed = [r for r in (_one_review(n) for n in raw_reviews) if r]
    if not parsed:
        for text in raw.get("reviews", []) or []:
            if _looks_like_review(text):
                parsed.append({"text": text})
    seen = set()
    unique = []
    for review in parsed:
        key = review.get("text")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(review)
    return unique[:50]


def _collect_images(raw: dict, meta: dict) -> list:
    product = _ld_product(_ld_nodes(raw))
    candidates = list((product or {}).get("image") or []) if isinstance((product or {}).get("image"), list) else []
    if isinstance((product or {}).get("image"), str):
        candidates.append(product.get("image"))
    og_image = meta.get("og:image") or meta.get("og:image:secure_url")
    if og_image:
        candidates.append(og_image)
    candidates.extend(raw.get("images", []) or [])
    seen = set()
    out = []
    for item in candidates:
        url = item if isinstance(item, str) else _first_str(item)
        if not url:
            continue
        url = url.strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        plain = url.split("?")[0].lower()
        if "/emoji/" in plain or plain.endswith(".svg"):
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= 30:
            break
    return out


def _site_name(url: str):
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return None
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


def _merge(raw: dict, requested_url: str, final_url: str) -> dict:
    meta = raw.get("meta", {}) or {}
    dom = raw.get("dom", {}) or {}
    product = _parse_product(raw)
    reviews = _parse_reviews(raw)
    price = product["price"] or meta.get("product:price:amount") or meta.get("og:price:amount") or dom.get("price")
    rating = product["rating"]
    if rating is None:
        rating = _to_float(dom.get("rating"))
    if rating is not None and not 0 <= rating <= 5:
        rating = None
    review_count = product["review_count"]
    return {
        "url": final_url or requested_url,
        "requested_url": requested_url,
        "source_site": _site_name(final_url or requested_url),
        "product_name": product["name"] or meta.get("og:title") or meta.get("twitter:title") or dom.get("title"),
        "merchant_name": product["seller"] or product["brand"] or dom.get("shop") or meta.get("og:site_name"),
        "sales_count": dom.get("sold"),
        "price": price,
        "price_value": _to_float(price),
        "currency": _detect_currency(price, product["currency"] or meta.get("product:price:currency") or meta.get("og:price:currency")),
        "rating": rating,
        "review_count": review_count,
        "reviews": reviews,
        "images": _collect_images(raw, meta),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


async def _run(url: str, headless: bool, scroll: bool, timeout_ms: int) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        try:
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1366, "height": 900},
                locale="id-ID",
                timezone_id="Asia/Jakarta",
                extra_http_headers={"Accept-Language": "id-ID,id;q=0.9,en;q=0.8"},
            )
            if _STEALTH_MODE == "v2" and Stealth is not None:
                try:
                    await Stealth().apply_stealth_async(context)
                except Exception:
                    pass
            page = await context.new_page()
            if _STEALTH_MODE == "v1" and stealth_async is not None:
                try:
                    await stealth_async(page)
                except Exception:
                    pass
            page.set_default_timeout(timeout_ms)
            await page.route("**/*", _block_resources)
            response = None
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                pass
            try:
                await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8000))
            except Exception:
                pass
            if scroll:
                await _autoscroll(page)
            raw = await page.evaluate(EXTRACT_JS)
            return {
                "raw": raw,
                "http_status": response.status if response is not None else None,
                "final_url": page.url,
            }
        finally:
            await browser.close()


async def _run_guarded(url, headless, scroll, timeout_ms, total_timeout_s) -> dict:
    if not isinstance(url, str) or not _is_safe_url(url):
        raise ValueError("invalid or disallowed url")
    timeout_ms = max(3000, min(int(timeout_ms), 120000))
    async with _SEM:
        return await asyncio.wait_for(
            _run(url, headless, scroll, timeout_ms), timeout=total_timeout_s
        )


async def scrape_ecommerce_data(
    url: str,
    headless: bool = True,
    scroll: bool = True,
    timeout_ms: int = 30000,
    total_timeout_s: float = 60.0,
) -> dict:
    result = await _run_guarded(url, headless, scroll, timeout_ms, total_timeout_s)
    data = _merge(result["raw"], url, result["final_url"])
    data["http_status"] = result["http_status"]
    return data


async def fetch_page_content(
    url: str,
    headless: bool = True,
    scroll: bool = True,
    timeout_ms: int = 30000,
    total_timeout_s: float = 60.0,
) -> dict:
    result = await _run_guarded(url, headless, scroll, timeout_ms, total_timeout_s)
    raw = result["raw"]
    meta = raw.get("meta", {}) or {}
    return {
        "url": result["final_url"] or url,
        "requested_url": url,
        "source_site": _site_name(result["final_url"] or url),
        "title": meta.get("og:title") or raw.get("dom", {}).get("title"),
        "text": raw.get("text", ""),
        "meta": meta,
        "images": _collect_images(raw, meta),
        "jsonld": raw.get("jsonld", []),
        "http_status": result["http_status"],
    }
