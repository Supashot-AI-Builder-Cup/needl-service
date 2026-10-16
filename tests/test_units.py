import pytest

from app.analysis.present import compact_count, format_rupiah
from app.analysis.scoring import risk_level, risk_score
from app.sources.parsing import native_price, parse_count, parse_rupiah
from app.sources.shopee_url import UnsupportedLink, is_allowed_host, parse_shopee_link


@pytest.mark.parametrize(
    "url,shop,item",
    [
        ("https://shopee.co.id/Serum-30ml-i.773019284.25810442671", "773019284", "25810442671"),
        ("https://shopee.co.id/Serum-30ml-i.773019284.25810442671?sp_atk=x&xptdk=y", "773019284", "25810442671"),
        ("https://www.shopee.co.id/product/63004127/23950618838", "63004127", "23950618838"),
        ("http://shopee.co.id/product/1/2/", "1", "2"),
    ],
)
def test_parse_product_links(url, shop, item):
    link = parse_shopee_link(url)
    assert (link.shop_id, link.item_id) == (shop, item)
    assert link.canonical_url == f"https://shopee.co.id/product/{shop}/{item}"


@pytest.mark.parametrize(
    "url,reason",
    [
        ("https://tokopedia.com/a-i.1.2", "host"),
        ("https://shopee.co.id.evil.com/a-i.1.2", "host"),
        ("ftp://shopee.co.id/a-i.1.2", "host"),
        ("https://shopee.co.id/search?keyword=x", "path"),
        ("https://shp.ee/", "path"),
    ],
)
def test_rejects_other_links(url, reason):
    with pytest.raises(UnsupportedLink) as exc:
        parse_shopee_link(url)
    assert exc.value.reason == reason


def test_browser_host_allowlist():
    assert is_allowed_host("down-id.img.susercontent.com")
    assert is_allowed_host("shopee.co.id")
    assert not is_allowed_host("169.254.169.254")
    assert not is_allowed_host("shopee.co.id.evil.com")


def test_number_formats():
    assert format_rupiah(39000) == "Rp39.000"
    assert compact_count(2400).model_dump() == {"en": "2.4K", "id": "2,4RB"}
    assert compact_count(18000).model_dump() == {"en": "18K", "id": "18RB"}
    assert parse_rupiah("Rp39.000 - Rp45.000") == 39000
    assert parse_count("1,2RB") == 1200
    assert parse_count("10RB+") == 10000
    assert parse_count("62,3k+") == 62300
    assert parse_count("1.234") == 1234
    assert native_price(3_900_000_000) == 39000
    assert native_price(12_900_000_000) == 129000


def test_score_zones():
    assert risk_level(0).value == "safe"
    assert risk_level(33).value == "suspicious"
    assert risk_level(67).value == "fraud"
    assert risk_score([]) == 0


def test_link_pasted_with_shell_escapes():
    # zsh escapes "?" and "=" when a URL is pasted inside double quotes.
    raw = (
        "https://shopee.co.id/SKINTIFIC-Niacinamide-Brightening-Series-5-in-1-Set-i.380266264.18705675288"
        "\\?extraParams\\=%7B%22display_model_id%22%3A253540827242%7D"
    )
    link = parse_shopee_link(raw)
    assert (link.shop_id, link.item_id) == ("380266264", "18705675288")
    assert link.slug_name == "SKINTIFIC Niacinamide Brightening Series 5 in 1 Set"
