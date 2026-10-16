import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.analysis.stub import StubAnalyzer
from app.api.errors import RequestIdMiddleware, install_error_handlers
from app.api.rate_limit import RateLimiter
from app.api.verification_jobs import router as verification_jobs_router
from app.config import Settings, load_settings
from app.db.repository import Repository
from app.jobs.queue import JobQueue
from app.jobs.runner import JobRunner, SourceRegistry
from app.legacy.routes import router as legacy_router
from app.llm import gemini
from app.sources.providers.apify import ApifyProvider
from app.sources.providers.base import ShopeeProvider
from app.sources.providers.rapidapi import RapidApiProvider
from app.sources.screenshots import ScreenshotSource
from app.sources.shopee import ShopeeSource
from app.sources.stub import StubSource

log = logging.getLogger("needl")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def configured_providers(settings: Settings) -> list[str]:
    """Names from SHOPEE_PROVIDERS that have credentials, in order."""
    ready = {
        "apify": bool(settings.apify_token),
        "rapidapi": bool(settings.rapidapi_key and settings.rapidapi_detail_url),
    }
    return [name for name in settings.shopee_providers if ready.get(name)]


def build_providers(settings: Settings, http: httpx.AsyncClient) -> list[ShopeeProvider]:
    providers: list[ShopeeProvider] = []
    for name in configured_providers(settings):
        if name == "apify":
            providers.append(
                ApifyProvider(
                    http,
                    settings.apify_token,
                    settings.apify_detail_actor,
                    settings.apify_reviews_actor,
                    settings.apify_timeout_s,
                    settings.apify_max_charge_usd,
                )
            )
        else:
            providers.append(
                RapidApiProvider(http, settings.rapidapi_key, settings.rapidapi_detail_url, settings.rapidapi_reviews_url)
            )
    return providers


def create_app(settings: Settings | None = None, http_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """`http_transport` lets tests replace outgoing HTTP calls."""
    settings = settings or load_settings()
    if settings.analyzer == "real":
        raise RuntimeError("NEEDL_ANALYZER=real is not implemented yet; use stub")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repo = await Repository.open(settings.db_path)
        http = httpx.AsyncClient(timeout=settings.http_timeout_s, transport=http_transport)
        if settings.source == "stub":
            sources = SourceRegistry(stub=StubSource(settings.stub_step_ms))
        else:
            providers = build_providers(settings, http)
            log.info("Shopee providers: %s", [p.name for p in providers] or "none configured")
            sources = SourceRegistry(
                link=ShopeeSource(providers, http, settings.shopee_max_reviews),
                screenshots=ScreenshotSource(),
            )
        runner = JobRunner(settings, repo, sources, StubAnalyzer(settings.stub_step_ms))
        queue = JobQueue(runner, settings.worker_count)

        app.state.settings = settings
        app.state.repo = repo
        app.state.queue = queue
        app.state.limiter = RateLimiter(
            {"create": settings.create_rate_per_min, "poll": settings.poll_rate_per_min},
            trust_forwarded_for=settings.trust_forwarded_for,
        )
        await queue.start(repo)
        try:
            yield
        finally:
            await queue.stop()
            await http.aclose()
            await repo.close()

    app = FastAPI(title="needl API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key"],
        expose_headers=["Location", "Retry-After", "X-Request-ID"],
    )
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "gemini_configured": gemini.is_configured(),
            "source": settings.source,
            "shopee_providers": configured_providers(settings),
        }

    app.include_router(verification_jobs_router)
    app.include_router(legacy_router)
    return app


app = create_app()
