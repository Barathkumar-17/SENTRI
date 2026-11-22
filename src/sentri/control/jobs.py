"""Background jobs started from the UI: planning a contract and running the attack grid.

One process-wide manager. Runs use the same engine as the CLI; their progress streams through the
existing event feed (`/api/events/stream`), so the UI watches a job exactly like a CLI run.
"""

from __future__ import annotations

import asyncio
import contextlib
import traceback
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

JobKind = Literal["plan", "run"]
JobStatus = Literal["queued", "running", "finished", "failed", "cancelled"]


@dataclass
class Job:
    id: str
    kind: JobKind
    status: JobStatus = "queued"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    run_id: int | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "run_id": self.run_id,
            "result": self.result,
            "error": self.error,
        }


class JobConflict(RuntimeError):
    """Raised when a second run is started while one is active."""


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    def active_run(self) -> Job | None:
        return next((j for j in self.jobs.values() if j.kind == "run" and j.status in ("queued", "running")), None)

    def start(self, kind: JobKind, work: Callable[[Job], Awaitable[dict[str, Any] | None]]) -> Job:
        if kind == "run" and self.active_run() is not None:
            raise JobConflict("a run is already in progress; wait for it or cancel it")
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)

        async def wrapper() -> None:
            job.status = "running"
            try:
                job.result = await work(job)
                job.status = "finished"
            except asyncio.CancelledError:
                job.status = "cancelled"
                raise
            except Exception as exc:  # noqa: BLE001 - reported to the UI, never crashes the server
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                job.result = {"traceback": traceback.format_exc(limit=5)}
            finally:
                job.finished_at = datetime.now(UTC).isoformat()

        job.task = asyncio.get_running_loop().create_task(wrapper())
        self.jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def cancel(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if job is not None and job.task is not None and not job.task.done():
            job.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await job.task
        return job
