"""In-process job queue. Needs a single server process (no uvicorn --workers)."""

import asyncio
import logging

from ..db.repository import Repository
from .runner import JobRunner

log = logging.getLogger("needl.jobs")


class JobQueue:
    def __init__(self, runner: JobRunner, worker_count: int):
        self.runner = runner
        self.worker_count = worker_count
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.tasks: list[asyncio.Task] = []

    async def start(self, repo: Repository) -> None:
        for job in await repo.unfinished_jobs():
            self.queue.put_nowait(job.id)
        self.tasks = [asyncio.create_task(self._work(), name=f"needl-worker-{i}") for i in range(self.worker_count)]

    async def put(self, job_id: str) -> None:
        await self.queue.put(job_id)

    async def join(self) -> None:
        await self.queue.join()

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []

    async def _work(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await self.runner.run(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("worker crashed on job %s", job_id)
            finally:
                self.queue.task_done()
