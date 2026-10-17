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


def _for_gemini(node):
    if isinstance(node, list):
        return [_for_gemini(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key == "discriminator":
            continue
        if key == "const":
            out["enum"] = [value]
        else:
            out["anyOf" if key == "oneOf" else key] = _for_gemini(value)
    return out


def response_json_schema(schema) -> dict:
    """JSON Schema of a Pydantic model in a form Gemini accepts and follows.

    Discriminated unions (such as the contract's Evidence) come out of Pydantic as
    `oneOf` plus `discriminator`, which the SDK rejects; `anyOf` lists the same
    choices. Each choice's tag (`kind: Literal["claims"]`) is a `const`, which Gemini
    does not enforce (it invented other kinds), so it becomes a one-value `enum`.
    The answer is still validated against the full Pydantic model.
    """
    return _for_gemini(schema.model_json_schema())


async def generate_structured(contents, schema, model=None, temperature=0.1):
    """Ask Gemini for JSON matching a Pydantic model and return the parsed model.

    Raises RuntimeError when Gemini is not configured, ValueError when the
    answer does not match the schema.
    """
    client = _client()
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        response_json_schema=response_json_schema(schema),
        temperature=temperature,
    )
    response = await client.aio.models.generate_content(
        model=model or DEFAULT_MODEL,
        contents=contents,
        config=config,
    )
    return schema.model_validate_json(response.text or "")


def image_part(data: bytes, mime_type: str):
    return types.Part.from_bytes(data=data, mime_type=mime_type)
