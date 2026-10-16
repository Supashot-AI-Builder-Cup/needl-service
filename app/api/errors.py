"""Request IDs and the application error envelope.

Application errors: {"detail": {"code", "message", "request_id"}}.
Request validation errors keep FastAPI's {"detail": [...]} shape.
"""

import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("needl.api")

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_request_id() -> str:
    """`req_` + a ULID (48-bit ms timestamp + 80 random bits, Crockford base32)."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "req_" + "".join(reversed(chars))


def request_id_of(request: Request) -> str:
    return getattr(request.state, "request_id", None) or new_request_id()


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


class RequestIdMiddleware:
    """Gives every request an ID (request.state.request_id and the X-Request-ID header)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = new_request_id()
        # Mutate in place so handlers outside this middleware see the same state.
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        await self.app(scope, receive, send_with_id)


INTERNAL_ERROR_MESSAGE = "An unexpected error occurred."


class UnhandledErrorMiddleware:
    """Turns an unhandled exception into the 500 envelope.

    Starlette answers unhandled exceptions from its outermost middleware, outside
    CORSMiddleware, so browsers block that response and the UI sees a network error.
    Registered inside CORSMiddleware, this one answers first, with CORS headers and
    X-Request-ID. Nothing from the exception reaches the client.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            if started:
                raise
            request = Request(scope)
            log.exception("unhandled error request_id=%s", request_id_of(request))
            response = error_response(request, 500, "internal_error", INTERNAL_ERROR_MESSAGE)
            await response(scope, receive, send)


def error_response(request: Request, status: int, code: str, message: str, headers=None) -> JSONResponse:
    body = {"detail": {"code": code, "message": message, "request_id": request_id_of(request)}}
    return JSONResponse(body, status_code=status, headers=headers)


_HTTP_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    415: "unsupported_media_type",
    429: "rate_limited",
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError):
        return error_response(request, exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return error_response(request, exc.status_code, code, message, getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception):
        # Fallback; UnhandledErrorMiddleware normally answers first.
        log.exception("unhandled error request_id=%s", request_id_of(request))
        return error_response(request, 500, "internal_error", INTERNAL_ERROR_MESSAGE)
