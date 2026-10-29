"""FFmpeg discovery, probing and validation.

Windows machines frequently have no FFmpeg on PATH. Resolution order:

1. ``FFMPEG_BINARY`` / ``FFPROBE_BINARY`` environment variables
2. the system PATH
3. the static binary bundled inside the ``imageio-ffmpeg`` wheel (verified in
   this project to be gyan.dev FFmpeg 7.1 with libx264 + native AAC)

``ffprobe`` is optional: when it is missing we fall back to parsing
``ffmpeg -i`` output, which exposes duration, stream codecs and dimensions
reliably enough for render verification.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from services.errors import DependencyError, RenderError
from services.logging_setup import get_logger

log = get_logger("ffmpeg")

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_VIDEO_RE = re.compile(r"Video:\s*([A-Za-z0-9_\-]+)")
_AUDIO_RE = re.compile(r"Audio:\s*([A-Za-z0-9_\-]+)")
_SIZE_RE = re.compile(r"(?<![\d.])(\d{2,5})x(\d{2,5})(?![\d.])")
_FPS_RE = re.compile(r"([\d.]+)\s*fps")
_HZ_RE = re.compile(r"(\d{4,6})\s*Hz")
_CHANNELS_RE = re.compile(r"\b(mono|stereo|(\d+) channels)\b")


def snap_to_frame(seconds: float, fps: int) -> float:
    """Round a duration to a whole number of frames.

    Encoded clips contain a whole number of frames, and xfade offsets are derived
    from timeline starts. Snapping here keeps picture, speech and crossfades from
    drifting apart by a few milliseconds per scene.
    """
    fps = max(1, int(fps))
    return max(1, round(float(seconds) * fps)) / fps



@dataclass
class MediaInfo:
    path: Path
    duration: float = 0.0
    has_video: bool = False
    has_audio: bool = False
    video_codec: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    audio_codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    size_bytes: int = 0
    raw: dict = field(default_factory=dict)

    @property
    def is_video(self) -> bool:
        return self.has_video

    def summary(self) -> str:
        bits = [f"{self.duration:.2f}s"]
        if self.has_video:
            bits.append(f"{self.video_codec} {self.width}x{self.height}@{self.fps or 0:g}fps")
        if self.has_audio:
            bits.append(f"{self.audio_codec} {self.sample_rate}Hz {self.channels}ch")
        return ", ".join(bits)


@functools.lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    override = os.environ.get("FFMPEG_BINARY", "").strip()
    if override:
        if Path(override).is_file():
            log.info("Using FFMPEG_BINARY override: %s", override)
            return override
        log.warning("FFMPEG_BINARY is set but does not exist; ignoring it.")

    system = shutil.which("ffmpeg")
    if system:
        return system

    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).is_file():
            log.info("Using bundled FFmpeg from imageio-ffmpeg.")
            return bundled
    except Exception as exc:  # pragma: no cover - import/runtime guard
        log.warning("Bundled FFmpeg unavailable: %s", exc)

    raise DependencyError(
        "FFmpeg was not found.",
        hint="Install FFmpeg (winget install Gyan.FFmpeg) or reinstall requirements.txt "
             "so the bundled binary is available, then restart the app.",
    )


@functools.lru_cache(maxsize=1)
def ffprobe_path() -> str | None:
    override = os.environ.get("FFPROBE_BINARY", "").strip()
    if override and Path(override).is_file():
        return override
    return shutil.which("ffprobe")


def version() -> str:
    try:
        out = run([ffmpeg_path(), "-hide_banner", "-version"], timeout=20)
        return out.stdout.splitlines()[0] if out.stdout else "unknown"
    except Exception as exc:  # pragma: no cover - diagnostic only
        return f"unavailable ({exc})"


def run(args: list[str], timeout: int = 120, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a binary and capture text output. Raises DependencyError if missing."""
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError as exc:
        raise DependencyError("A required media tool is missing.",
                              hint="Install FFmpeg or reinstall requirements.txt.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderError("The media operation took too long and was stopped.",
                          hint="Try fewer/shorter images, or retry.",
                          log_detail=f"timeout after {timeout}s: {args[:2]}") from exc


def probe(path: str | Path) -> MediaInfo:
    """Return normalised media information. Raises RenderError if unreadable."""
    target = Path(path)
    if not target.is_file():
        raise RenderError("A required media file is missing.",
                          log_detail=f"probe: missing file {target}")
    info = MediaInfo(path=target, size_bytes=target.stat().st_size)

    probe_bin = ffprobe_path()
    if probe_bin:
        try:
            result = run([probe_bin, "-v", "error", "-print_format", "json",
                          "-show_format", "-show_streams", str(target)], timeout=60)
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                _fill_from_ffprobe(info, data)
                info.raw = data
                return info
        except Exception as exc:  # fall through to ffmpeg parsing
            log.debug("ffprobe failed, falling back to ffmpeg parsing: %s", exc)

    _fill_from_ffmpeg_stderr(info)
    return info


def _fill_from_ffprobe(info: MediaInfo, data: dict) -> None:
    fmt = data.get("format", {}) or {}
    try:
        info.duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        info.duration = 0.0
    for stream in data.get("streams", []) or []:
        kind = stream.get("codec_type")
        if kind == "video" and not info.has_video:
            info.has_video = True
            info.video_codec = stream.get("codec_name")
            info.width = int(stream.get("width") or 0) or None
            info.height = int(stream.get("height") or 0) or None
            info.fps = _safe_float(stream.get("avg_frame_rate"))
        elif kind == "audio" and not info.has_audio:
            info.has_audio = True
            info.audio_codec = stream.get("codec_name")
            info.sample_rate = int(stream.get("sample_rate") or 0) or None
            info.channels = int(stream.get("channels") or 0) or None
    if not info.duration:
        for stream in data.get("streams", []) or []:
            duration = _safe_float(stream.get("duration"))
            if duration:
                info.duration = duration
                break


def _fill_from_ffmpeg_stderr(info: MediaInfo) -> None:
    result = run([ffmpeg_path(), "-hide_banner", "-i", str(info.path)], timeout=60)
    text = (result.stderr or "") + (result.stdout or "")
    if not text.strip():
        raise RenderError("The media file could not be read.",
                          log_detail=f"no ffmpeg output for {info.path}")

    match = _DURATION_RE.search(text)
    if match:
        hours, minutes, seconds = match.groups()
        info.duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    for line in text.splitlines():
        stripped = line.strip()
        if "Stream #" not in stripped:
            continue
        if "Video:" in stripped and not info.has_video:
            info.has_video = True
            vm = _VIDEO_RE.search(stripped)
            info.video_codec = vm.group(1) if vm else None
            sm = _SIZE_RE.search(stripped)
            if sm:
                info.width, info.height = int(sm.group(1)), int(sm.group(2))
            fm = _FPS_RE.search(stripped)
            info.fps = float(fm.group(1)) if fm else None
        elif "Audio:" in stripped and not info.has_audio:
            info.has_audio = True
            am = _AUDIO_RE.search(stripped)
            info.audio_codec = am.group(1) if am else None
            hm = _HZ_RE.search(stripped)
            info.sample_rate = int(hm.group(1)) if hm else None
            cm = _CHANNELS_RE.search(stripped)
            if cm:
                info.channels = 1 if cm.group(1) == "mono" else (2 if cm.group(1) == "stereo"
                                                               else int(cm.group(2) or 0) or None)

    if not info.has_video and not info.has_audio:
        raise RenderError("The media file has no readable audio or video stream.",
                          log_detail=f"unrecognised media: {info.path}")


def _safe_float(value) -> float | None:
    try:
        if value in (None, "", "0/0"):
            return None
        if isinstance(value, str) and "/" in value:
            num, den = value.split("/", 1)
            return round(float(num) / float(den), 3) if float(den) else None
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def duration_of(path: str | Path) -> float:
    info = probe(path)
    if info.duration <= 0:
        raise RenderError("The media file has no measurable duration.",
                          log_detail=f"zero duration: {path}")
    return info.duration


def decode_check(path: str | Path, timeout: int = 180) -> bool:
    """Fully decode the file without writing output; True when FFmpeg reports no errors."""
    result = run([ffmpeg_path(), "-v", "error", "-i", str(path), "-f", "null", "-"], timeout=timeout)
    errors = (result.stderr or "").strip()
    if result.returncode != 0:
        log.warning("Decode check failed (%s): %s", result.returncode, errors[:500])
        return False
    if errors:
        log.warning("Decode check reported errors: %s", errors[:500])
        return False
    return True


def disk_free_mb(path: str | Path) -> int:
    """Free space in MB, walking up to the nearest existing directory.

    Work directories are created lazily, so the exact path may not exist yet;
    checking its parent is still meaningful and avoids a false "no space" error.
    """
    candidate = Path(path)
    for _ in range(6):
        try:
            if candidate.exists():
                return int(shutil.disk_usage(str(candidate)).free / (1024 * 1024))
        except OSError:
            pass
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return 0


def run_cancellable(args: list[str], should_cancel=None, timeout: int = 600,
                    poll_seconds: float = 0.25) -> subprocess.CompletedProcess:
    """Run a long media command, polling so the user can cancel it.

    Raises CancelledError when ``should_cancel()`` turns true, and RenderError on
    timeout. Output is buffered because FFmpeg is invoked with ``-v error``, so
    the pipes cannot fill and deadlock.
    """
    import time as _time

    from services.errors import CancelledError

    if should_cancel and should_cancel():
        raise CancelledError()
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW
    else:
        flags = 0
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace",
                                   creationflags=flags)
    except FileNotFoundError as exc:
        raise DependencyError("A required media tool is missing.",
                              hint="Install FFmpeg or reinstall requirements.txt.") from exc

    started = _time.time()
    while process.poll() is None:
        if should_cancel and should_cancel():
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            raise CancelledError()
        if _time.time() - started > timeout:
            process.kill()
            raise RenderError("The media operation took too long and was stopped.",
                              hint="Try a shorter video or fewer photos.",
                              log_detail=f"timeout after {timeout}s")
        _time.sleep(poll_seconds)

    stdout, stderr = process.communicate()
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def has_filter(name: str) -> bool:
    try:
        out = run([ffmpeg_path(), "-hide_banner", "-filters"], timeout=20).stdout
        return f" {name} " in out.replace("\n", " ")
    except Exception:
        return False
