"""In-process job tracking.

Video rendering takes 10–40 seconds, which is too long for one HTTP request.
A job is created immediately, work runs in a background thread, and the browser
polls ``/api/jobs/<id>`` for stage status.

Progress is *derived from completed stages*, never from a timer: a stage is only
marked ``done`` after its work actually finished, so the UI cannot show 100%
while FFmpeg is still running.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.storage import cleanup, new_id

STAGES: list[tuple[str, str]] = [
    ("validate", "Validating property information"),
    ("images", "Preparing images"),
    ("script", "Generating script"),
    ("narration", "Generating voice-over"),
    ("music", "Preparing background music"),
    ("render", "Rendering video"),
    ("verify", "Verifying the MP4"),
    ("ready", "Ready to preview and download"),
]
STAGE_IDS = [stage_id for stage_id, _ in STAGES]

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"


@dataclasses.dataclass
class Job:
    id: str
    work_dir: Path
    status: str = "queued"
    created_at: str = dataclasses.field(default_factory=lambda: _now())
    finished_at: str | None = None
    stages: list[dict[str, str]] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)
    error: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    # Filesystem paths for serving media stay here and are NEVER serialised:
    # the browser only receives opaque /video/<id> style URLs.
    media: dict[str, str] = dataclasses.field(default_factory=dict)
    cancel_event: threading.Event = dataclasses.field(default_factory=threading.Event)
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if not self.stages:
            self.stages = [{"id": stage_id, "label": label, "status": STATUS_PENDING,
                            "detail": ""} for stage_id, label in STAGES]

    # -- state ------------------------------------------------------------- #
    def begin(self) -> None:
        with self.lock:
            self.status = "running"

    def set_stage(self, stage_id: str, status: str, detail: str = "") -> None:
        with self.lock:
            for stage in self.stages:
                if stage["id"] == stage_id:
                    stage["status"] = status
                    if detail:
                        stage["detail"] = detail
                    break

    def complete_stage(self, stage_id: str, detail: str = "") -> None:
        self.set_stage(stage_id, STATUS_DONE, detail)

    def skip_stage(self, stage_id: str, detail: str = "Not requested") -> None:
        self.set_stage(stage_id, STATUS_SKIPPED, detail)

    def add_warning(self, message: str) -> None:
        message = " ".join(str(message).split())
        if not message:
            return
        with self.lock:
            if message not in self.warnings:
                self.warnings.append(message)

    def fail(self, error: dict[str, Any]) -> None:
        with self.lock:
            self.error = error
            self.status = "cancelled" if error.get("code") == "cancelled" else "failed"
            self.finished_at = _now()
            for stage in self.stages:
                if stage["status"] == STATUS_RUNNING:
                    stage["status"] = STATUS_FAILED
                    break

    def succeed(self, result: dict[str, Any]) -> None:
        with self.lock:
            self.result = result
            self.status = "done"
            self.finished_at = _now()
            for stage in self.stages:
                if stage["status"] in (STATUS_PENDING, STATUS_RUNNING):
                    stage["status"] = STATUS_DONE

    def cancel(self) -> bool:
        if self.status in ("done", "failed", "cancelled"):
            return False
        self.cancel_event.set()
        return True

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def progress_percent(self) -> int:
        total = len([s for s in self.stages if s["status"] != STATUS_SKIPPED]) or 1
        done = len([s for s in self.stages if s["status"] == STATUS_DONE])
        return int(round(100 * min(done, total) / total))

    def to_public(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.id,
                "status": self.status,
                "created_at": self.created_at,
                "finished_at": self.finished_at,
                "stages": [dict(stage) for stage in self.stages],
                "warnings": list(self.warnings),
                "error": dict(self.error) if self.error else None,
                "result": dict(self.result) if self.result else None,
                "progress": self.progress_percent(),
                "cancellable": self.status in ("queued", "running"),
            }


class JobStore:
    def __init__(self, max_jobs: int = 40):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._max_jobs = max_jobs

    def create(self, work_root: Path) -> Job:
        job_id = new_id()
        work_dir = work_root / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        job = Job(id=job_id, work_dir=work_dir)
        with self._lock:
            self._jobs[job_id] = job
            self._prune()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def active(self) -> Job | None:
        with self._lock:
            for job in self._jobs.values():
                if job.status in ("queued", "running"):
                    return job
        return None

    def _prune(self) -> None:
        if len(self._jobs) <= self._max_jobs:
            return
        finished = sorted(
            (job for job in self._jobs.values() if job.status in ("done", "failed", "cancelled")),
            key=lambda job: job.created_at,
        )
        for job in finished[: len(self._jobs) - self._max_jobs]:
            if job.id != "sample":
                cleanup(job.work_dir)
            self._jobs.pop(job.id, None)

    def cleanup_workdir(self, job: Job) -> None:
        """Remove intermediates but keep the final MP4 (it is served from here)."""
        try:
            for name in ("scenes", "clips", "audio", "narration"):
                cleanup(job.work_dir / name)
        except Exception:
            pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RenderLock:
    """One render at a time: FFmpeg saturates CPU, and serialising is predictable."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 0.1) -> bool:
        return self._lock.acquire(timeout=timeout)

    def release(self) -> None:
        try:
            self._lock.release()
        except RuntimeError:
            pass


jobs = JobStore()
render_lock = RenderLock()
