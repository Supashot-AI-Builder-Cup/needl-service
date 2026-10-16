import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

SYSTEM_INSTRUCTION = (
    "You extract structured data from scraped e-commerce product pages. "
    "Respond ONLY with a single valid JSON object. Never add markdown fences or commentary."
)

DEFAULT_INSTRUCTION = (
    "You are a shopping assistant. Analyze the scraped product page below and "
    "produce a product report. Return ONE JSON object with exactly these keys:\n"
    "- product_name: string|null\n"
    "- price: string|null (keep original, e.g. \"IDR 322,561\")\n"
    "- price_value: number|null\n"
    "- currency: ISO 4217 code|null\n"
    "- merchant_name: string|null (store or marketplace)\n"
    "- rating: number 0-5|null\n"
    "- review_count: string|null (as shown, e.g. \"62,3k+\")\n"
    "- overview: string (2-4 sentences: what the product is and how it works)\n"
    "- key_benefits: array of {title, description} (e.g. Skin Repair, Key Ingredients, "
    "Versatility, Who it is for)\n"
    "- price_range_indonesia: string (typical price range in Indonesia across "
    "Shopee/Tokopedia/pharmacies, with the Rupiah range)\n"
    "- reviews: array of {author, rating, text}\n"
    "- images: array of image URLs\n"
    "- report: string (a human-readable Markdown report for the user, with the "
    "product name + price header, an overview paragraph, a \"Key Benefits & Features\" "
    "section with bullet points, and a \"Price Range in Indonesia\" section)\n"
    "Use null for unknown values. Do not invent specific reviews. You may estimate "
    "the Indonesian price range from general knowledge."
)

IMAGE_INSTRUCTION = (
    "You are a shopping assistant. The user provides one or more images "
    "(screenshots of a product page and/or its reviews). Analyze ONLY what is "
    "visible in the images and return ONE JSON object with exactly these keys:\n"
    "- product_name: string|null\n"
    "- product_summary: string (what the product is and its main purpose)\n"
    "- overall_rating: number 0-5|null\n"
    "- review_count: string|null (as shown)\n"
    "- sentiment: \"positive\"|\"mixed\"|\"negative\"|null\n"
    "- rating_breakdown: array of {stars (integer 1-5), count (string|null), "
    "summary (string), pros (array of strings), complaints (array of strings)}\n"
    "- reviews: array of {author (string|null), rating (number 0-5|null), "
    "text (string), sentiment (string), complaints (array), suggestions (array)}\n"
    "- top_complaints: array of strings (most common complaints/keluhan)\n"
    "- suggestions: array of strings (saran/perbaikan tied to the complaints)\n"
    "- report: string (a Markdown report: product summary, then the reviews "
    "grouped by star rating, each with its complaints and suggestions)\n"
    "Group reviews by their star rating and judge relevance from the text. "
    "Use null or empty arrays when the data is not visible. Write the text fields "
    "in the same language as the user's additional instruction, otherwise Indonesian."
)


def is_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))


def _client():
    if genai is None:
        raise RuntimeError("google-genai is not installed")
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    return genai.Client(api_key=api_key)


def _build_contents(url: str, content: dict, prompt) -> str:
    parts = [f"URL: {url}"]
    if content.get("title"):
        parts.append(f"PAGE TITLE: {content['title']}")
    if content.get("source_site"):
        parts.append(f"SITE: {content['source_site']}")
    meta = content.get("meta") or {}
    keep = (
        "og:title",
        "og:description",
        "og:site_name",
        "description",
        "product:price:amount",
        "product:price:currency",
    )
    compact = {k: meta[k] for k in keep if meta.get(k)}
    if compact:
        parts.append("META: " + json.dumps(compact, ensure_ascii=False))
    images = content.get("images") or []
    if images:
        parts.append("PAGE IMAGES: " + json.dumps(images[:20], ensure_ascii=False))
    text = (content.get("text") or "")[:15000]
    parts.append("PAGE TEXT:\n" + text)
    instruction = DEFAULT_INSTRUCTION
    if prompt and prompt.strip():
        instruction += "\n\nADDITIONAL USER INSTRUCTION (prioritize this, but keep the JSON keys):\n" + prompt.strip()
    parts.append("INSTRUCTION:\n" + instruction)
    return "\n\n".join(parts)


def _parse_json(text: str):
    text = (text or "").strip()
    if not text:
        return {"text": None}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cleaned = text.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return {"text": text}


async def analyze_page(url: str, content: dict, prompt=None, model=None):
    client = _client()
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        temperature=0.2,
    )
    response = await client.aio.models.generate_content(
        model=model or DEFAULT_MODEL,
        contents=_build_contents(url, content, prompt),
        config=config,
    )
    try:
        text = response.text
    except Exception:
        text = None
    return _parse_json(text or "")


async def analyze_images(images, prompt=None, model=None):
    client = _client()
    instruction = IMAGE_INSTRUCTION
    if prompt and prompt.strip():
        instruction += "\n\nADDITIONAL USER INSTRUCTION (prioritize this, but keep the JSON keys):\n" + prompt.strip()
    contents = [instruction]
    for data, mime_type in images:
        contents.append(types.Part.from_bytes(data=data, mime_type=mime_type))
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        temperature=0.2,
    )
    response = await client.aio.models.generate_content(
        model=model or DEFAULT_MODEL,
        contents=contents,
        config=config,
    )
    try:
        text = response.text
    except Exception:
        text = None
    return _parse_json(text or "")


async def generate_structured(contents, schema, model=None, temperature=0.1):
    """Ask Gemini for JSON matching a Pydantic model and return the parsed model.

    Raises RuntimeError when Gemini is not configured, ValueError when the
    answer does not match the schema.
    """
    client = _client()
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=temperature,
    )
    response = await client.aio.models.generate_content(
        model=model or DEFAULT_MODEL,
        contents=contents,
        config=config,
    )
    if isinstance(response.parsed, schema):
        return response.parsed
    return schema.model_validate_json(response.text or "")


def image_part(data: bytes, mime_type: str):
    return types.Part.from_bytes(data=data, mime_type=mime_type)
