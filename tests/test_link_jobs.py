from app.contract.jobs import VerificationJobResponse
from tests.conftest import JOBS, RISKY_LINK, SAFE_LINK, wait_for_job


async def test_link_job_runs_to_completion(client):
    res = await client.post(JOBS, json={"url": RISKY_LINK, "locale": "id-ID"})
    assert res.status_code == 202
    body = res.json()
    job_id = body["data"]["id"]
    assert body["data"]["status"] == "pending"
    assert body["data"]["status_url"] == f"{JOBS}/{job_id}"
    assert body["data"]["poll_after_seconds"] == 2
    assert body["meta"]["request_id"].startswith("req_")
    assert res.headers["location"] == body["data"]["status_url"]
    assert res.headers["retry-after"] == "2"

    data = await wait_for_job(client, job_id)
    assert data["status"] == "completed"
    assert data["progress"] == 100
    assert data["error"] is None
    assert data["preview"]["product"]["store"] == "DermaIune 0fficial Shop"
    result = data["verification"]
    assert data["verification_id"] == result["id"]
    assert result["product"]["score"] == 84
    assert result["trust_score"] == 16
    assert result["risk_level"] == "fraud"
    assert result["product"]["verdict"] == "high"
    assert result["product"]["sold"] == {"en": "2.4K", "id": "2,4RB"}
    assert result["product"]["ratings"] == "1,2RB"
    assert result["source"]["canonical_url"] == "https://shopee.co.id/product/773019284/25810442671"
    assert [c["code"] for c in result["checks"]][:2] == ["store-name", "risky-terms"]
    assert len(result["stores"]) == 3 and len(result["questions"]) == 5
    assert result["cached"] is False

    # The whole response must satisfy the contract models.
    final = await client.get(f"{JOBS}/{job_id}")
    VerificationJobResponse.model_validate(final.json())
    assert final.headers["cache-control"] == "no-store"
    assert "retry-after" not in final.headers


async def test_safe_link_gets_safe_sample(client):
    res = await client.post(JOBS, json={"url": SAFE_LINK})
    data = await wait_for_job(client, res.json()["data"]["id"])
    result = data["verification"]
    assert result["risk_level"] == "safe"
    assert result["product"]["verdict"] == "safe"
    assert result["product"]["mall"] is True
    assert result["stores"] == []


async def test_slug_name_becomes_display_name(client):
    url = "https://shopee.co.id/Glowlab-Vitamin-C-20-Serum-20ml-i.554120.88123?sp_atk=abc"
    res = await client.post(JOBS, json={"url": url})
    data = await wait_for_job(client, res.json()["data"]["id"])
    assert data["verification"]["displayName"] == "Glowlab Vitamin C 20 Serum 20ml"
    assert data["preview"]["displayName"] == "Glowlab Vitamin C 20 Serum 20ml"


async def test_second_scan_of_same_product_uses_cache(client):
    first = await client.post(JOBS, json={"url": RISKY_LINK})
    first_data = await wait_for_job(client, first.json()["data"]["id"])
    # Same product, different URL form.
    second = await client.post(JOBS, json={"url": "https://shopee.co.id/product/773019284/25810442671"})
    second_data = await wait_for_job(client, second.json()["data"]["id"])
    assert second_data["status"] == "completed"
    assert second_data["verification_id"] == first_data["verification_id"]
    assert second_data["verification"]["cached"] is True
    assert second_data["preview"]["displayName"] == first_data["verification"]["displayName"]


async def test_unsupported_host_is_422(client):
    res = await client.post(JOBS, json={"url": "https://www.tokopedia.com/shop/item"})
    assert res.status_code == 422
    assert res.json() == {
        "detail": [
            {
                "loc": ["body", "url"],
                "msg": "URL host is not a supported marketplace",
                "type": "value_error",
                "input": "https://www.tokopedia.com/shop/item",
            }
        ]
    }


async def test_shopee_page_that_is_not_a_product_is_422(client):
    res = await client.post(JOBS, json={"url": "https://shopee.co.id/search?keyword=serum"})
    assert res.status_code == 422
    assert res.json()["detail"][0]["msg"] == "URL is not a Shopee product link"


async def test_bad_link_bodies(client):
    missing = await client.post(JOBS, json={"locale": "id-ID"})
    assert missing.status_code == 422
    assert missing.json()["detail"][0]["loc"] == ["body", "url"]
    assert missing.json()["detail"][0]["type"] == "missing"

    extra = await client.post(JOBS, json={"url": RISKY_LINK, "foo": 1})
    assert extra.status_code == 422
    assert extra.json()["detail"][0]["type"] == "extra_forbidden"

    locale = await client.post(JOBS, json={"url": RISKY_LINK, "locale": "fr-FR"})
    assert locale.status_code == 422

    broken = await client.post(JOBS, content=b"{nope", headers={"content-type": "application/json"})
    assert broken.status_code == 400
    assert broken.json()["detail"]["code"] == "invalid_json"


async def test_json_with_screenshot_fields_is_mixed_input(client):
    res = await client.post(JOBS, json={"url": RISKY_LINK, "product": "x"})
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["code"] == "mixed_input"
    assert detail["request_id"].startswith("req_")


async def test_other_content_types_are_415(client):
    res = await client.post(JOBS, content=b"url=x", headers={"content-type": "text/plain"})
    assert res.status_code == 415
    assert res.json()["detail"]["code"] == "unsupported_media_type"


async def test_short_link_is_accepted(client):
    res = await client.post(JOBS, json={"url": "https://id.shp.ee/AbCd123"})
    assert res.status_code == 202


async def test_link_with_query_params_is_accepted(client):
    url = (
        "https://shopee.co.id/SKINTIFIC-Niacinamide-Brightening-Series-5-in-1-Set-i.380266264.18705675288"
        "?extraParams=%7B%22display_model_id%22%3A253540827242%2C%22model_selection_logic%22%3A2%7D"
    )
    res = await client.post(JOBS, json={"url": url})
    assert res.status_code == 202
    data = await wait_for_job(client, res.json()["data"]["id"])
    assert data["verification"]["source"]["canonical_url"] == "https://shopee.co.id/product/380266264/18705675288"
