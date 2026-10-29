"""Path safety and filesystem helpers.

Rules enforced here:

* User-supplied filenames are never used as paths. Uploads land in a fresh UUID
  directory and every derived asset gets a name the app chooses.
* ``safe_join`` refuses anything that resolves outside the allowed root, which
  blocks ``../`` traversal and Windows drive-absolute tricks.
* Media URLs are built from opaque ids (job / library ids), never from real
  filesystem paths, so the browser never learns the server layout.
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from services.errors import SecurityError

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
UPLOAD_DIR = BASE_DIR / "uploads"
MUSIC_DIR = BASE_DIR / "music_library"
VIDEO_DIR = BASE_DIR / "generated_videos"
SAMPLE_DIR = BASE_DIR / "sample_data"
LOG_DIR = BASE_DIR / "logs"
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_UUID_RE = re.compile(r"^[0-9a-f]{32}$")


def ensure_dirs() -> None:
    for directory in (CONFIG_DIR, UPLOAD_DIR, MUSIC_DIR, VIDEO_DIR, VIDEO_DIR / "samples", LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def is_id(value: str) -> bool:
    """True for the opaque 32-char hex ids this app generates."""
    return bool(value) and bool(_UUID_RE.match(value))


def new_id() -> str:
    return uuid.uuid4().hex


def safe_join(root: Path, *parts: str) -> Path:
    """Join under ``root`` or raise SecurityError.

    Used for every path that is influenced by a request.
    """
    root_resolved = root.resolve()
    candidate = root_resolved
    for part in parts:
        if part is None or part == "":
            continue
        if Path(str(part)).is_absolute() or re.match(r"^[A-Za-z]:", str(part)):
            raise SecurityError("Unsafe path rejected.", log_detail=f"absolute part: {part!r}")
        candidate = candidate / str(part)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise SecurityError(
            "Unsafe path rejected.", log_detail=f"escaped root: {resolved}"
        ) from exc
    return resolved


def safe_original_name(filename: str, fallback: str = "upload") -> str:
    """Return a display-only filename: basename, no control chars, length capped."""
    if not filename:
        return fallback
    name = str(filename).replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '\r\n\t')
    name = name.strip().strip(".") or fallback
    return name[:120]


def extension_of(filename: str) -> str:
    return Path(str(filename or "")).suffix.lower()


def slugify(text: str, fallback: str = "property") -> str:
    slug = _SLUG_RE.sub("-", (text or "").lower()).strip("-")
    return (slug or fallback)[:60]


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def cleanup(path: Path | None) -> None:
    """Best-effort recursive delete. Never raises: cleanup must not mask errors."""
    if not path:
        return
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink(missing_ok=True)
    except OSError:
        pass


def write_text_atomic(path: Path, text: str, mode: int = 0o600) -> None:
    """Write text via a temp file + replace, with restrictive permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        tmp.chmod(mode)
    except OSError:
        pass
    tmp.replace(path)


def write_bytes_atomic(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    try:
        tmp.chmod(mode)
    except OSError:
        pass
    tmp.replace(path)
