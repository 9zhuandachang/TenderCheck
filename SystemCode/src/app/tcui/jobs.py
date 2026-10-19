"""Background job runner.

Long tasks (parsing, extraction, review) run in a thread and write progress to the database, so page
reruns and refreshes only read state and never start the same inference twice.
"""

import logging
import threading
import traceback
from typing import Any, Callable

from . import store

log = logging.getLogger(__name__)
_submit_lock = threading.Lock()


class Progress:
    def __init__(self, job_id: int):
        self.job_id = job_id

    def __call__(self, stage: str, done: int = 0, total: int = 0) -> None:
        store.update("jobs", self.job_id, {"stage": stage, "done": done, "total": total})


def submit(project_id: int, kind: str, fn: Callable[..., Any], *args: Any) -> int:
    """Start fn(progress, *args) in the background unless a job of the same kind is already active."""
    with _submit_lock:
        existing = store.active_job(project_id, kind)
        if existing:
            return existing["id"]
        job_id = store.create_job(project_id, kind)

    def run() -> None:
        store.update("jobs", job_id, {"status": "running", "stage": "Starting"})
        try:
            result = fn(Progress(job_id), *args)
            store.update("jobs", job_id, {"status": "done", "result": result or {}, "finished_at": store.now()})
        except Exception as exc:  # surfaced to the user with a retry entry
            log.error("Job %s (%s) failed:\n%s", job_id, kind, traceback.format_exc())
            store.update("jobs", job_id, {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "finished_at": store.now(),
            })

    threading.Thread(target=run, name=f"tendercheck-{kind}-{job_id}", daemon=True).start()
    return job_id
