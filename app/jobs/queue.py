"""In-process job queue plus a sweeper for jobs left behind by another process.

Jobs created here are queued directly. The sweeper periodically looks in Firestore for
pending or processing jobs that nobody has touched for `stale_after_s` (their worker
died, for example during a restart or deploy) and queues them again. JobRunner claims a
job in a transaction before running it, so a job is not run twice even when two
instances overlap for a moment.
"""

import asyncio
import logging
from datetime import timedelta

from ..db.models import utc_now
from ..db.repository import Repository
from .runner import JobRunner

log = logging.getLogger("needl.jobs")


class JobQueue:
    def __init__(self, runner: JobRunner, worker_count: int, stale_after_s: float, sweep_interval_s: float):
        self.runner = runner
        self.worker_count = worker_count
        self.stale_after_s = stale_after_s
        self.sweep_interval_s = sweep_interval_s
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.tasks: list[asyncio.Task] = []
        # Jobs queued or running in this process; put() ignores them.
        self._known: set[str] = set()

    async def start(self, repo: Repository) -> None:
        self.tasks = [asyncio.create_task(self._work(), name=f"needl-worker-{i}") for i in range(self.worker_count)]
        self.tasks.append(asyncio.create_task(self._sweep_forever(repo), name="needl-sweeper"))

    async def put(self, job_id: str) -> None:
        if job_id in self._known:
            return
        self._known.add(job_id)
        await self.queue.put(job_id)

    async def join(self) -> None:
        await self.queue.join()

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []

    async def sweep(self, repo: Repository) -> None:
        """Queue unfinished jobs that no worker has updated recently."""
        stale_before = utc_now() - timedelta(seconds=self.stale_after_s)
        for job in await repo.unfinished_jobs():
            if job.updated_at < stale_before and job.id not in self._known:
                log.info("requeueing stale job %s (%s since %s)", job.id, job.status, job.updated_at.isoformat())
                await self.put(job.id)

    @staticmethod
    def _cancelling() -> bool:
        task = asyncio.current_task()
        return task is not None and task.cancelling() > 0

    async def _sweep_forever(self, repo: Repository) -> None:
        while True:
            try:
                await self.sweep(repo)
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._cancelling():
                    raise asyncio.CancelledError()
                log.exception("job sweep failed")
            await asyncio.sleep(self.sweep_interval_s)

    async def _work(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await self.runner.run(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._cancelling():
                    # A library turned our cancellation into another error: still stop.
                    raise asyncio.CancelledError()
                log.exception("worker crashed on job %s", job_id)
            finally:
                self._known.discard(job_id)
                self.queue.task_done()
