from tests.conftest import JOBS, image_bytes, wait_for_job


def files(**extra):
    base = {
        "product": ("product.png", image_bytes("PNG"), "image/png"),
        "store": ("store.jpg", image_bytes("JPEG"), "image/jpeg"),
    }
    base.update(extra)
    return base


async def test_screenshot_job_runs_and_cleans_up(client, settings):
    res = await client.post(
        JOBS,
        files=[
            *files(description=("d.webp", image_bytes("WEBP"), "image/webp")).items(),
            ("extra_screenshots", ("x1.png", image_bytes(), "image/png")),
            ("extra_screenshots", ("x2.png", image_bytes(), "image/png")),
        ],
        data={"extra_names": ["Packaging", "Seal"], "locale": "id-ID"},
    )
    assert res.status_code == 202, res.text
    data = await wait_for_job(client, res.json()["data"]["id"])
    assert data["status"] == "completed"
    source = data["verification"]["source"]
    assert source == {
        "kind": "screenshots",
        "url": None,
        "canonical_url": None,
        "marketplace": None,
        "fetched_at": None,
        "screenshot_count": 5,
    }
    # Uploaded files are deleted once the job is done.
    assert not any(settings.upload_dir.rglob("*.*"))


async def test_missing_required_screenshot_is_422(client):
    res = await client.post(JOBS, files={"product": ("p.png", image_bytes(), "image/png")})
    assert res.status_code == 422
    assert res.json() == {"detail": [{"loc": ["body", "store"], "msg": "Field required", "type": "missing"}]}


async def test_url_in_multipart_is_mixed_input(client):
    res = await client.post(JOBS, files=files(), data={"url": "https://shopee.co.id/product/1/2"})
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "mixed_input"


async def test_fake_image_is_415(client):
    res = await client.post(JOBS, files=files(store=("store.png", b"just text, not an image", "image/png")))
    assert res.status_code == 415
    assert res.json()["detail"]["code"] == "unsupported_image_type"


async def test_gif_is_415(client):
    res = await client.post(JOBS, files=files(store=("store.gif", image_bytes("GIF"), "image/gif")))
    assert res.status_code == 415


async def test_file_over_10_mib_is_413(client):
    big = image_bytes() + b"\0" * (10 * 1024 * 1024)
    res = await client.post(JOBS, files=files(store=("store.png", big, "image/png")))
    assert res.status_code == 413
    assert res.json()["detail"]["code"] == "file_too_large"


async def test_content_length_over_limit_is_413(client):
    res = await client.post(
        JOBS,
        content=b"x",
        headers={"content-type": "multipart/form-data; boundary=abc", "content-length": str(60 * 1024 * 1024)},
    )
    assert res.status_code == 413


async def test_too_many_extras_is_422(client):
    extras = [("extra_screenshots", (f"x{i}.png", image_bytes(), "image/png")) for i in range(6)]
    res = await client.post(JOBS, files=[*files().items(), *extras])
    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "extra_screenshots"]


async def test_extra_names_must_match_extras(client):
    res = await client.post(
        JOBS,
        files=[*files().items(), ("extra_screenshots", ("x.png", image_bytes(), "image/png"))],
        data={"extra_names": ["One", "Two"]},
    )
    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "extra_names"]


async def test_unknown_multipart_field_is_422(client):
    res = await client.post(JOBS, files=files(), data={"colour": "red"})
    assert res.status_code == 422
    assert res.json()["detail"][0]["type"] == "extra_forbidden"
