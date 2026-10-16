"""Unexpected errors still reach the browser as the 500 envelope, with nothing technical in it."""

import httpx

from main import create_app
from tests.conftest import UI_ORIGIN

SECRET = "database password is hunter2"


async def test_unhandled_error_is_500_envelope_with_cors_and_request_id(settings):
    app = create_app(settings)

    async def boom():
        raise RuntimeError(SECRET)

    app.add_api_route("/boom", boom)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/boom", headers={"Origin": UI_ORIGIN})

    assert res.status_code == 500
    assert res.headers["access-control-allow-origin"] == UI_ORIGIN
    detail = res.json()["detail"]
    assert detail["code"] == "internal_error"
    assert detail["message"] == "An unexpected error occurred."
    assert detail["request_id"] == res.headers["x-request-id"]
    assert SECRET not in res.text and "RuntimeError" not in res.text
