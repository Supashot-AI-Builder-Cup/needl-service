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
    "Respond ONLY with valid JSON matching the requested schema."
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
