"""Minimal background job runner for long tasks (separation, kit building)."""

from __future__ import annotations

import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .store import new_id

log = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    kind: str
    project_id: str
    status: str = "pending"  # pending | running | done | error
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def update(self, progress: float | None = None, message: str | None = None) -> None:
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        if message is not None:
            self.message = message


class JobRunner:
    def __init__(self, workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chopper-job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, project_id: str, fn: Callable[[Job], Any]) -> Job:
        job = Job(id=new_id(), kind=kind, project_id=project_id)
        with self._lock:
            self._jobs[job.id] = job

        def run():
            job.status = "running"
            job.started_at = time.time()
            try:
                job.result = fn(job)
                job.progress = 1.0
                job.status = "done"
            except Exception as exc:  # surfaced to the UI
                log.error("job %s failed:\n%s", job.id, traceback.format_exc())
                job.error = str(exc) or exc.__class__.__name__
                job.status = "error"
            finally:
                job.finished_at = time.time()

        self._pool.submit(run)
        return job

    def get(self, jid: str) -> Job | None:
        return self._jobs.get(jid)

    def for_project(self, pid: str) -> list[dict]:
        return [asdict(j) for j in self._jobs.values() if j.project_id == pid]
