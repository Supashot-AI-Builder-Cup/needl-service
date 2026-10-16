import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

APIFY_DETAIL_ACTOR = "zen-studio~shopee-product-detail-scraper"
APIFY_REVIEWS_ACTOR = "zen-studio~shopee-product-reviews-scraper"


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


def _env_path(name: str, default: str) -> Path:
    return Path(_env(name, default)).expanduser().resolve()


def _env_optional(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _env_list(name: str, default: str) -> list[str]:
    return [part.strip() for part in _env(name, default).split(",") if part.strip()]


MIB = 1024 * 1024
# Cloud Run rejects HTTP/1 requests over 32 MiB before they reach the app (and without
# CORS headers). 30 MiB of images plus multipart overhead stays below that.
MAX_UPLOAD_TOTAL_MB = 30


@dataclass(frozen=True)
class Settings:
    upload_dir: Path
    # stub: sample data without network; live: Shopee data APIs or Gemini Vision.
    source: Literal["stub", "live"]
    # stub: sample checks from the UI mock; real: the analyzers from the AI plan.
    analyzer: Literal["stub", "real"]
    stub_step_ms: int
    worker_count: int
    job_timeout_s: float
    max_attempts: int
    cache_ttl_s: int
    idempotency_ttl_s: int
    create_rate_per_min: int
    poll_rate_per_min: int
    trust_forwarded_for: bool
    cors_origins: list[str] = field(default_factory=list)
    # Shopee data providers, tried in this order. Unconfigured ones are skipped.
    shopee_providers: list[str] = field(default_factory=lambda: ["apify", "rapidapi"])
    shopee_max_reviews: int = 100
    apify_token: str | None = None
    apify_detail_actor: str = APIFY_DETAIL_ACTOR
    apify_reviews_actor: str = APIFY_REVIEWS_ACTOR
    apify_timeout_s: int = 120
    apify_max_charge_usd: float | None = None
    rapidapi_key: str | None = None
    # Full URL templates with {shop_id}, {item_id}, {url} and {limit} placeholders.
    rapidapi_detail_url: str | None = None
    rapidapi_reviews_url: str | None = None
    http_timeout_s: int = 30
    # Firestore. None lets the client library take the project from ADC (or the emulator).
    firestore_project: str | None = None
    firestore_database: str = "(default)"
    # A processing job not updated for this long belongs to a dead worker and may be taken over.
    job_stale_after_s: float = 240.0
    job_sweep_interval_s: float = 60.0
    # With trust_forwarded_for, the client IP is this many entries from the right of X-Forwarded-For.
    trusted_proxy_hops: int = 1
    # /scrape, /analyze and /analyze-image need Playwright with Chromium.
    legacy_routes: bool = True
    max_upload_total_bytes: int = MAX_UPLOAD_TOTAL_MB * MIB

    def with_overrides(self, **changes) -> "Settings":
        return replace(self, **changes)


def load_settings() -> Settings:
    source = _env("NEEDL_SOURCE", "stub")
    analyzer = _env("NEEDL_ANALYZER", "stub")
    if source not in ("stub", "live"):
        raise ValueError("NEEDL_SOURCE must be stub or live")
    if analyzer not in ("stub", "real"):
        raise ValueError("NEEDL_ANALYZER must be stub or real")
    max_charge = _env_optional("APIFY_MAX_CHARGE_USD")
    job_timeout_s = float(_env_int("JOB_TIMEOUT_S", 180))
    proxy_hops = _env_int("TRUSTED_PROXY_HOPS", 1)
    if proxy_hops < 1:
        raise ValueError("TRUSTED_PROXY_HOPS must be 1 or more")
    upload_mb = _env_int("MAX_UPLOAD_TOTAL_MB", MAX_UPLOAD_TOTAL_MB)
    if not 1 <= upload_mb <= MAX_UPLOAD_TOTAL_MB:
        raise ValueError(f"MAX_UPLOAD_TOTAL_MB must be between 1 and {MAX_UPLOAD_TOTAL_MB}")
    return Settings(
        upload_dir=_env_path("UPLOAD_DIR", str(BASE_DIR / "data" / "uploads")),
        source=source,
        analyzer=analyzer,
        stub_step_ms=_env_int("STUB_STEP_MS", 600),
        worker_count=_env_int("WORKER_COUNT", 2),
        job_timeout_s=job_timeout_s,
        max_attempts=_env_int("JOB_MAX_ATTEMPTS", 2),
        cache_ttl_s=_env_int("CACHE_TTL_S", 6 * 3600),
        idempotency_ttl_s=_env_int("IDEMPOTENCY_TTL_S", 24 * 3600),
        create_rate_per_min=_env_int("RATE_CREATE_PER_MIN", 10),
        poll_rate_per_min=_env_int("RATE_POLL_PER_MIN", 60),
        trust_forwarded_for=_env_bool("TRUST_FORWARDED_FOR", False),
        # Browsers send Origin without a trailing slash; "https://x.com/" would never match.
        cors_origins=[o.rstrip("/") for o in _env_list("CORS_ORIGINS", "http://localhost:5173")],
        shopee_providers=_env_list("SHOPEE_PROVIDERS", "apify,rapidapi"),
        shopee_max_reviews=_env_int("SHOPEE_MAX_REVIEWS", 100),
        apify_token=_env_optional("APIFY_TOKEN"),
        apify_detail_actor=_env("APIFY_DETAIL_ACTOR", APIFY_DETAIL_ACTOR),
        apify_reviews_actor=_env("APIFY_REVIEWS_ACTOR", APIFY_REVIEWS_ACTOR),
        apify_timeout_s=_env_int("APIFY_TIMEOUT_S", 120),
        apify_max_charge_usd=float(max_charge) if max_charge else None,
        rapidapi_key=_env_optional("RAPIDAPI_KEY"),
        rapidapi_detail_url=_env_optional("RAPIDAPI_SHOPEE_DETAIL_URL"),
        rapidapi_reviews_url=_env_optional("RAPIDAPI_SHOPEE_REVIEWS_URL"),
        http_timeout_s=_env_int("HTTP_TIMEOUT_S", 30),
        firestore_project=_env_optional("GOOGLE_CLOUD_PROJECT"),
        firestore_database=_env("FIRESTORE_DATABASE", "(default)"),
        job_stale_after_s=float(_env_int("JOB_STALE_AFTER_S", int(job_timeout_s) + 60)),
        job_sweep_interval_s=float(_env_int("JOB_SWEEP_INTERVAL_S", 60)),
        trusted_proxy_hops=proxy_hops,
        legacy_routes=_env_bool("ENABLE_LEGACY_ROUTES", True),
        max_upload_total_bytes=upload_mb * MIB,
    )
