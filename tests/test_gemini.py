"""Gemini requests built by the real google-genai client, with only the HTTP call mocked.

Other tests replace generate_structured entirely, so they never build a request. These
build it, which is where an unsupported schema fails (as AnalysisResponse did).
"""

import json

import httpx
import pytest
from google import genai
from google.genai import types

from app.analysis.gemini import CHECK_SECTIONS, AnalysisResponse, GeminiAnalyzer, contract_checks
from app.contract.result import Check, ClaimsEvidence, NameCompareEvidence
from app.llm import gemini
from app.sources.base import RawListing
from app.sources.screenshots import ScreenshotExtraction

ANALYSIS_ANSWER = {
    "checks": [
        {
            "code": "store-name",
            "section": "store",
            "severity": "minor",
            "name": {"en": "Store name", "id": "Nama toko"},
            "summary": {"en": "Not the brand's official store.", "id": "Bukan toko resmi brand."},
            "evidence": [
                {"kind": "nameCompare", "official": "ANUA Official Shop", "store": "ROVIA Store.ID", "facts": []}
            ],
        },
        {
            "code": "claims",
            "section": "listing",
            "severity": "pass",
            "name": "Claims",
            "summary": "No BPOM number given.",
            "evidence": [
                {"kind": "claims", "claims": [{"name": "BPOM", "detail": "Not stated", "status": "minor", "label": "Missing"}]}
            ],
        },
    ],
    "questions": [{"en": "Can you share the BPOM number?", "id": "Boleh minta nomor BPOM?"}],
}


def gemini_reply(answer) -> dict:
    text = answer if isinstance(answer, str) else json.dumps(answer)
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}]}


@pytest.fixture
def fake_gemini(monkeypatch):
    """Route the real SDK client to a mock; returns the captured request bodies."""
    sent: list[dict] = []

    def install(answer):
        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(json.loads(request.content))
            return httpx.Response(200, json=gemini_reply(answer))

        def client():
            http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return genai.Client(api_key="test-key", http_options=types.HttpOptions(httpx_async_client=http))

        monkeypatch.setattr(gemini, "_client", client)
        return sent

    return install


def keywords(node) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in keywords(v)}
    if isinstance(node, list):
        return {k for v in node for k in keywords(v)}
    return set()


@pytest.mark.parametrize("schema", [AnalysisResponse, ScreenshotExtraction])
def test_schemas_have_no_keywords_gemini_rejects_or_ignores(schema):
    found = keywords(gemini.response_json_schema(schema))
    assert not found & {"oneOf", "discriminator", "const"}


def test_evidence_kinds_are_enums_gemini_follows():
    defs = gemini.response_json_schema(AnalysisResponse)["$defs"]
    assert defs["ClaimsEvidence"]["properties"]["kind"]["enum"] == ["claims"]
    assert defs["NameCompareEvidence"]["properties"]["kind"]["enum"] == ["nameCompare"]


def test_check_codes_are_limited_to_the_ui_steps():
    defs = gemini.response_json_schema(AnalysisResponse)["$defs"]
    assert set(defs["AnalyzedCheck"]["properties"]["code"]["enum"]) == set(CHECK_SECTIONS)


def test_invented_check_code_is_rejected():
    answer = {"checks": [{**ANALYSIS_ANSWER["checks"][0], "code": "DESC_AUTHENTICITY_CLAIM"}]}
    with pytest.raises(ValueError):
        AnalysisResponse.model_validate(answer)


def test_contract_checks_keep_one_per_code_in_its_section():
    first, second = ANALYSIS_ANSWER["checks"]
    misplaced = {**second, "section": "reviews"}
    duplicate = {**first, "severity": "critical"}
    data = AnalysisResponse.model_validate({"checks": [first, misplaced, duplicate]})
    checks = contract_checks(data.checks)
    assert [(c.code, c.section.value, c.severity.value) for c in checks] == [
        ("store-name", "store", "minor"),
        ("claims", "listing", "pass"),
    ]
    assert all(type(c) is Check for c in checks)


async def test_analysis_request_is_built_and_answer_parsed(fake_gemini):
    sent = fake_gemini(ANALYSIS_ANSWER)
    data = await gemini.generate_structured(["listing facts"], AnalysisResponse)

    config = sent[0]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == gemini.response_json_schema(AnalysisResponse)
    assert "responseSchema" not in config
    # The discriminated union still picks the right evidence class from `kind`.
    assert isinstance(data.checks[0].evidence[0], NameCompareEvidence)
    assert isinstance(data.checks[1].evidence[0], ClaimsEvidence)


async def test_screenshot_request_is_built_and_answer_parsed(fake_gemini):
    fake_gemini({"product_name": "ANUA Gua Sha Cream", "price": "Rp251.478", "reviews": [{"stars": 5, "text": "Bagus"}]})
    data = await gemini.generate_structured(["screenshots"], ScreenshotExtraction)
    assert data.product_name == "ANUA Gua Sha Cream"
    assert data.reviews[0].stars == 5


async def test_answer_outside_the_contract_is_a_value_error(fake_gemini):
    fake_gemini({"checks": [{"code": "x", "section": "store", "severity": "major", "name": "n", "summary": "s",
                             "evidence": [{"kind": "madeUp"}]}]})
    with pytest.raises(ValueError):
        await gemini.generate_structured(["x"], AnalysisResponse)


async def test_gemini_analyzer_runs_end_to_end(fake_gemini):
    sent = fake_gemini(ANALYSIS_ANSWER)
    listing = RawListing(
        source_kind="url",
        title="ANUA Collagen Retinol Refining Gua Sha Cream 80ml",
        price=251478,
        price_was=600000,
        rating=4.9,
        rating_count=15,
        sold_count=73,
        shop_name="ROVIA Store.ID",
        is_mall=False,
        shop_location="KOTA JAKARTA SELATAN",
    )
    progress: list[int] = []

    async def report(value: int) -> None:
        progress.append(value)

    output = await GeminiAnalyzer().run(listing, report)
    assert [c.code for c in output.checks] == ["store-name", "claims"]
    assert output.questions and progress == [45, 65]
    assert "ROVIA Store.ID" in sent[0]["contents"][0]["parts"][0]["text"]
