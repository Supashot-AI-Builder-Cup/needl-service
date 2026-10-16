"""Runs one verification job: source -> preview -> analysis -> saved result."""

import asyncio
import logging
from datetime import timedelta

from ..analysis.base import AnalysisError, Analyzer
from ..analysis.build import build_result
from ..analysis.present import build_preview, display_name
from ..config import Settings
from ..contract.jobs import JobPreview
from ..db.repository import JobRecord, Repository, utc_now
from ..sources.base import RawListing, Source, SourceError
from ..sources.screenshot_files import remove_upload
from . import progress as P

log = logging.getLogger("needl.jobs")

FAILURE_MESSAGES = {
    "timeout": "The check took too long. Try again.",
    "interrupted": "The check was interrupted. Try again.",
    "analysis_failed": "The check failed. Try again.",
    "internal_error": "An unexpected error occurred. Try again.",
}


class SourceRegistry:
    """Picks the source for a job: stub, Shopee browser, or screenshots."""

    def __init__(self, stub: Source | None = None, link: Source | None = None, screenshots: Source | None = None):
        self.stub = stub
        self.link = link
        self.screenshots = screenshots

    def for_job(self, job: JobRecord) -> Source:
        source = self.stub or (self.link if job.input_kind == "url" else self.screenshots)
        if source is None:
            raise SourceError("source_unavailable", "This input type is not available right now.")
        return source


class JobRunner:
    def __init__(self, settings: Settings, repo: Repository, sources: SourceRegistry, analyzer: Analyzer):
        self.settings = settings
        self.repo = repo
        self.sources = sources
        self.analyzer = analyzer

    async def run(self, job_id: str) -> None:
        job = await self.repo.get_job(job_id)
        if job is None or job.status in ("completed", "failed"):
            return
        if job.attempts >= self.settings.max_attempts:
            await self._fail(job, "interrupted")
            return
        await self.repo.update_job(job.id, status="processing", progress=P.STARTED, attempts=job.attempts + 1)

        finished = False
        try:
            await asyncio.wait_for(self._process(job), timeout=self.settings.job_timeout_s)
            finished = True
        except asyncio.TimeoutError:
            await self._fail(job, "timeout")
            finished = True
        except SourceError as exc:
            log.info("job %s source error %s", job.id, exc.code)
            await self._fail(job, exc.code, exc.message)
            finished = True
        except AnalysisError:
            log.exception("job %s analysis failed", job.id)
            await self._fail(job, "analysis_failed")
            finished = True
        except asyncio.CancelledError:
            # Server shutting down: the job stays "processing" and is requeued on the next start.
            raise
        except Exception:
            log.exception("job %s failed unexpectedly", job.id)
            await self._fail(job, "internal_error")
            finished = True
        finally:
            if finished and job.input_kind == "screenshots":
                remove_upload(job.input.get("dir"))

    async def _process(self, job: JobRecord) -> None:
        last = P.STARTED

        async def report(value: int) -> None:
            nonlocal last
            if value > last:
                last = value
                await self.repo.update_job(job.id, progress=min(value, P.SAVING))

        if job.canonical_url and await self._complete_from_cache(job, job.canonical_url):
            return

        listing = await self.sources.for_job(job).fetch(job, report)
        if job.input_kind == "url" and listing.canonical_url and listing.canonical_url != job.canonical_url:
            # A short link resolved to a product: check the cache again with the real URL.
            await self.repo.update_job(job.id, canonical_url=listing.canonical_url)
            if await self._complete_from_cache(job, listing.canonical_url):
                return

        await self.repo.update_job(job.id, preview=_preview_json(listing), progress=P.PREVIEW)
        last = P.PREVIEW

        analysis = await self.analyzer.run(listing, report)
        result = build_result(listing, analysis)
        await report(P.SAVING)
        await self.repo.insert_verification(result)
        await self.repo.update_job(job.id, status="completed", progress=P.DONE, verification_id=str(result.id))

    async def _complete_from_cache(self, job: JobRecord, canonical_url: str) -> bool:
        newer_than = utc_now() - timedelta(seconds=self.settings.cache_ttl_s)
        verification_id = await self.repo.find_fresh_verification(canonical_url, newer_than)
        if verification_id is None:
            return False
        result = await self.repo.get_verification(verification_id)
        preview = JobPreview(
            displayName=result.displayName,
            product=result.product.model_dump(exclude={"score", "verdict"}),
        )
        await self.repo.update_job(
            job.id,
            status="completed",
            progress=P.DONE,
            preview=preview.model_dump(mode="json"),
            verification_id=verification_id,
            from_cache=True,
        )
        return True

    async def _fail(self, job: JobRecord, code: str, message: str | None = None) -> None:
        await self.repo.update_job(
            job.id,
            status="failed",
            error_code=code,
            error_message=message or FAILURE_MESSAGES.get(code, FAILURE_MESSAGES["internal_error"]),
        )


def _preview_json(listing: RawListing) -> dict:
    preview = JobPreview(displayName=display_name(listing), product=build_preview(listing))
    return preview.model_dump(mode="json")
