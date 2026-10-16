"""Parse numbers the way Shopee prints them (Rp39.000, 1,2RB, 2.4K, 10RB+)."""

import re

_MULTIPLIERS = {"rb": 1_000, "k": 1_000, "jt": 1_000_000, "m": 1_000_000}


def parse_rupiah(text: str | None) -> int | None:
    """"Rp39.000" -> 39000. A range like "Rp39.000 - Rp45.000" gives the first price."""
    if not text:
        return None
    match = re.search(r"\d[\d.,]*", text)
    if not match:
        return None
    digits = re.sub(r"[.,](\d{3})", r"\1", match.group(0))
    digits = digits.split(",")[0].split(".")[0]
    return int(digits) if digits.isdigit() else None


def parse_count(text: str | int | None) -> int | None:
    """"1,2RB" -> 1200, "2.4K" -> 2400, "10RB+" -> 10000, "1.234" -> 1234."""
    if text is None:
        return None
    if isinstance(text, int):
        return text
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(rb|jt|k|m)?", text.lower())
    if not match:
        return None
    number, unit = match.groups()
    if unit:
        return int(round(float(number.replace(",", ".")) * _MULTIPLIERS[unit]))
    return int(number.replace(".", "").replace(",", ""))


def native_price(value: int | float | None) -> int | None:
    """Shopee's own web API stores prices multiplied by 100000."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return int(value) // 100_000


def shopee_image_url(image_hash: str | None) -> str | None:
    if not image_hash:
        return None
    if image_hash.startswith("http"):
        return image_hash
    return f"https://down-id.img.susercontent.com/file/{image_hash}"
