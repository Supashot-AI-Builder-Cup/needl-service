"""Turn raw numbers into the display strings the UI shows."""

from ..contract.common import LocalizedText
from ..contract.result import ProductPreview
from ..sources.base import RawListing


def format_rupiah(value: int | None) -> str:
    if value is None:
        return "-"
    return "Rp" + f"{value:,}".replace(",", ".")


def _compact(value: float, decimal: str) -> str:
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return text.replace(".", decimal)


def compact_count(n: int | None) -> LocalizedText:
    """2400 -> 2.4K / 2,4RB, the way Shopee shows counts."""
    if n is None:
        return LocalizedText(en="-", id="-")
    if n < 1000:
        return LocalizedText(en=str(n), id=str(n))
    if n < 1_000_000:
        return LocalizedText(en=_compact(n / 1000, ".") + "K", id=_compact(n / 1000, ",") + "RB")
    return LocalizedText(en=_compact(n / 1_000_000, ".") + "M", id=_compact(n / 1_000_000, ",") + "JT")


def display_name(listing: RawListing) -> str:
    return listing.display_name or listing.title


def build_preview(listing: RawListing) -> ProductPreview:
    return ProductPreview(
        name=listing.title,
        price=format_rupiah(listing.price),
        was=format_rupiah(listing.price_was) if listing.price_was else None,
        rating=f"{listing.rating:.1f}" if listing.rating is not None else "-",
        ratings=compact_count(listing.rating_count).id,
        sold=compact_count(listing.sold_count),
        store=listing.shop_name or "-",
        mall=bool(listing.is_mall),
        loc=listing.shop_location or "-",
        image=listing.image_urls[0] if listing.image_urls else None,
    )
