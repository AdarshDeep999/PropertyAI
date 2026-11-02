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
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.storage import UPLOAD_DIR, cleanup, new_id

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
    # Progress inside the stage that is currently running (0.0 - 1.0). Scene
    # rendering uses it so the bar keeps moving between stage boundaries.
    fraction: float = 0.0
    cancel_event: threading.Event = dataclasses.field(default_factory=threading.Event)
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)

    @property
    def state_path(self) -> Path:
        return Path(self.work_dir) / "job.json"

    def __post_init__(self) -> None:
        if not self.stages:
            self.stages = [{"id": stage_id, "label": label, "status": STATUS_PENDING,
                            "detail": ""} for stage_id, label in STAGES]

    # -- state ------------------------------------------------------------- #
    def begin(self) -> None:
        with self.lock:
            self.status = "running"
            self._save_locked()

    def set_stage(self, stage_id: str, status: str, detail: str = "") -> None:
        with self.lock:
            for stage in self.stages:
                if stage["id"] == stage_id:
                    stage["status"] = status
                    if detail:
                        stage["detail"] = detail
                    self.fraction = 1.0 if status in (STATUS_DONE, STATUS_SKIPPED) else 0.0
                    break
            self._save_locked()

    def set_progress(self, stage_id: str, detail: str, fraction: float) -> None:
        """Update the detail text and the completed fraction of a running stage."""
        with self.lock:
            for stage in self.stages:
                if stage["id"] == stage_id:
                    stage["status"] = STATUS_RUNNING
                    if detail:
                        stage["detail"] = detail
                    break
            self.fraction = max(0.0, min(1.0, float(fraction)))
            self._save_locked()

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
                self._save_locked()

    def fail(self, error: dict[str, Any]) -> None:
        with self.lock:
            self.error = error
            self.status = "cancelled" if error.get("code") == "cancelled" else "failed"
            self.finished_at = _now()
            for stage in self.stages:
                if stage["status"] == STATUS_RUNNING:
                    stage["status"] = STATUS_FAILED
                    break
            self._save_locked()

    def succeed(self, result: dict[str, Any]) -> None:
        with self.lock:
            self.result = result
            self.status = "done"
            self.finished_at = _now()
            for stage in self.stages:
                if stage["status"] in (STATUS_PENDING, STATUS_RUNNING):
                    stage["status"] = STATUS_DONE
            self.fraction = 1.0
            self._save_locked()

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
        running = self.fraction if any(s["status"] == STATUS_RUNNING for s in self.stages) else 0.0
        return int(round(100 * min(done + running, total) / total))

    # -- persistence ------------------------------------------------------- #
    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "stages": [dict(stage) for stage in self.stages],
            "warnings": list(self.warnings),
            "error": dict(self.error) if self.error else None,
            "result": dict(self.result) if self.result else None,
            "media": dict(self.media),
            "fraction": self.fraction,
        }

    def _save_locked(self) -> None:
        """Persist the job so a page refresh (or a process restart) can find it."""
        try:
            path = self.state_path
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.snapshot()), encoding="utf-8")
            tmp.replace(path)
        except OSError:  # persistence is a convenience, never a render blocker
            pass

    @classmethod
    def from_snapshot(cls, data: dict[str, Any], work_dir: Path) -> "Job":
        job = cls(id=str(data.get("id") or work_dir.name), work_dir=work_dir)
        job.status = str(data.get("status") or "queued")
        job.created_at = str(data.get("created_at") or _now())
        job.finished_at = data.get("finished_at")
        if isinstance(data.get("stages"), list) and data["stages"]:
            job.stages = [dict(stage) for stage in data["stages"]]
        job.warnings = [str(item) for item in data.get("warnings") or []]
        job.error = data.get("error") if isinstance(data.get("error"), dict) else None
        job.result = data.get("result") if isinstance(data.get("result"), dict) else None
        job.media = data.get("media") if isinstance(data.get("media"), dict) else {}
        job.fraction = float(data.get("fraction") or 0.0)
        return job

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
    def __init__(self, work_root: Path | None = None, max_jobs: int = 40):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._max_jobs = max_jobs
        self._work_root = Path(work_root) if work_root else None
        if self._work_root is not None:
            self.load_existing()

    def load_existing(self) -> None:
        """Re-attach jobs written by a previous process.

        A render cannot survive a restart, so an interrupted job is reported as
        failed instead of silently hanging on the progress screen forever.
        Finished videos are only kept if their MP4 is still on disk.
        """
        root = self._work_root
        if root is None or not root.is_dir():
            return
        for path in sorted(root.glob("*/job.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            try:
                job = Job.from_snapshot(data, path.parent)
            except (TypeError, ValueError):
                continue
            if job.status in ("queued", "running"):
                job.fail({
                    "code": "interrupted",
                    "message": "This video stopped when the app restarted.",
                    "hint": "Your details are still in the form — generate it again.",
                    "retryable": True,
                })
            elif job.status == "done":
                video = Path(str((job.media or {}).get("video", "")))
                if not video.is_file():
                    job.fail({
                        "code": "expired",
                        "message": "That video is no longer stored on the server.",
                        "hint": "Generate it again — it only takes a minute.",
                        "retryable": True,
                    })
            with self._lock:
                self._jobs[job.id] = job
        with self._lock:
            self._prune()

    def create(self, work_root: Path) -> Job:
        job_id = new_id()
        work_dir = work_root / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        job = Job(id=job_id, work_dir=work_dir)
        job._save_locked()
        with self._lock:
            self._jobs[job_id] = job
            self._prune()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None or self._work_root is None:
                return job
            state = self._work_root / job_id / "job.json"
            if not state.is_file():
                return None
            try:
                data = json.loads(state.read_text(encoding="utf-8"))
                job = Job.from_snapshot(data, state.parent)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                return None
            self._jobs[job.id] = job
            return job

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


jobs = JobStore(UPLOAD_DIR / "jobs")
render_lock = RenderLock()
