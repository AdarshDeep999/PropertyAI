"""Background music library.

Three modes are supported by the UI, and this module backs all of them:

* library track  -> ``add_to_library`` then reuse by id
* single-video   -> ``save_temp_track`` (file is deleted with the job)
* no music       -> nothing is called, and no replacement track is invented

Every audio file is decoded with FFmpeg before it is accepted, so a renamed
``.mp3`` that is actually text is rejected with a clear message.
"""

from __future__ import annotations

import dataclasses
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from services import ffmpeg_tools
from services.errors import MusicError, NotFoundError, ValidationError
from services.logging_setup import get_logger
from services.storage import AUDIO_EXTENSIONS, MUSIC_DIR, extension_of, human_size, new_id, safe_original_name

log = get_logger("music")
INDEX_PATH = MUSIC_DIR / "index.json"
_LOCK = threading.RLock()

RIGHTS_NOTICE = (
    "Only upload music you have the right to use. The app does not provide or generate "
    "background music, and you are responsible for licensing any track you add."
)


@dataclasses.dataclass
class Track:
    id: str
    name: str  # original display name
    stored_name: str
    duration: float
    size: int
    added_at: str
    title: str = ""
    artist: str = ""
    album: str = ""

    def to_public(self) -> dict:
        label_parts = [part for part in (self.artist, self.title) if part]
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.name,
            "duration": round(self.duration, 2),
            "duration_label": _duration_label(self.duration),
            "size": self.size,
            "size_label": human_size(self.size),
            "added_at": self.added_at,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "metadata_label": " – ".join(label_parts) if label_parts else "",
            "audio_url": f"/media/music/{self.id}",
        }


def _duration_label(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _load_index() -> list[Track]:
    with _LOCK:
        try:
            if INDEX_PATH.is_file():
                raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
                tracks = []
                for item in raw.get("tracks", []):
                    try:
                        tracks.append(Track(**item))
                    except TypeError:
                        continue
                return tracks
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Music index unreadable (%s); starting empty.", exc)
    return []


def _save_index(tracks: list[Track]) -> None:
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "tracks": [dataclasses.asdict(track) for track in tracks]}
    tmp = INDEX_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(INDEX_PATH)


def list_tracks() -> list[dict]:
    return [track.to_public() for track in _load_index()]


def get_track(track_id: str) -> Track:
    for track in _load_index():
        if track.id == track_id:
            return track
    raise NotFoundError("That music track is no longer in the library.")


def track_path(track_id: str) -> Path:
    track = get_track(track_id)
    path = MUSIC_DIR / track.stored_name
    if not path.is_file():
        raise NotFoundError("That music file is missing from the library.")
    return path


def _read_metadata(path: Path) -> dict:
    """Read title/artist/album if present. Never fatal: metadata is a nicety."""
    try:
        from mutagen import File as MutagenFile

        # WAV/ID3 tags are not exposed through the "easy" mapping, so try both.
        audio = MutagenFile(path, easy=True)
        if audio:
            def first(key: str) -> str:
                values = audio.get(key) or []
                return str(values[0]).strip() if values else ""
            metadata = {key: first(key)[:120] for key in ("title", "artist", "album")}
            if any(metadata.values()):
                return metadata

        raw = MutagenFile(str(path))
        if raw is None:
            return {}

        def first_raw(keys: tuple[str, ...]) -> str:
            for key in keys:
                value = raw.get(key)
                if not value:
                    continue
                if isinstance(value, list):
                    value = value[0] if value else ""
                text = getattr(value, "text", value)
                if isinstance(text, list):
                    text = text[0] if text else ""
                text = str(text).strip()
                if text:
                    return text[:120]
            return ""

        return {
            "title": first_raw(("TIT2", "title", "\xa9nam")),
            "artist": first_raw(("TPE1", "artist", "\xa9ART")),
            "album": first_raw(("TALB", "album", "\xa9alb")),
        }
    except Exception as exc:  # metadata is a nicety, never a blocker
        log.debug("No metadata for %s: %s", path.name, exc)
        return {}


def _validate_audio(path: Path, limits: dict) -> float:
    max_mb = int(limits.get("max_audio_mb", 25))
    if path.stat().st_size > max_mb * 1024 * 1024:
        raise MusicError(f"That audio file is larger than {max_mb} MB.",
                         hint="Use a shorter track or a lower bitrate MP3.")
    try:
        info = ffmpeg_tools.probe(path)
    except Exception as exc:
        raise MusicError("That audio file could not be read.",
                         hint="Try an MP3 or WAV file; corrupt files cannot be used.") from exc
    if not info.has_audio:
        raise MusicError("That file does not contain an audio track.",
                         hint="Choose a music file such as MP3 or WAV.")
    if info.duration < float(limits.get("min_audio_seconds", 1.0)):
        raise MusicError("That audio file is too short to use as background music.",
                         hint="Use a track at least a few seconds long.")
    return info.duration


def add_to_library(filename: str, data: bytes, limits: dict) -> dict:
    """Validate and store a track permanently, returning its public record."""
    name = safe_original_name(filename)
    extension = extension_of(name)
    if extension not in AUDIO_EXTENSIONS:
        raise MusicError(f"'{name}' is not a supported audio type.",
                         hint="Use MP3, WAV, M4A, AAC, OGG or FLAC.")
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    track_id = new_id()
    stored = MUSIC_DIR / f"{track_id}{extension}"
    stored.write_bytes(data)
    try:
        duration = _validate_audio(stored, limits)
    except Exception:
        stored.unlink(missing_ok=True)
        raise
    metadata = _read_metadata(stored)
    track = Track(
        id=track_id, name=name, stored_name=stored.name, duration=round(duration, 3),
        size=stored.stat().st_size,
        added_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **metadata,
    )
    with _LOCK:
        tracks = _load_index()
        tracks.append(track)
        _save_index(tracks)
    log.info("Added music track %s (%s).", track.id, name)
    return track.to_public()


def save_temp_track(filename: str, data: bytes, dest_dir: Path, limits: dict) -> Path:
    """Store a one-off track for this job only (not added to the library)."""
    name = safe_original_name(filename)
    extension = extension_of(name)
    if extension not in AUDIO_EXTENSIONS:
        raise MusicError(f"'{name}' is not a supported audio type.",
                         hint="Use MP3, WAV, M4A, AAC, OGG or FLAC.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"music{extension}"
    path.write_bytes(data)
    try:
        _validate_audio(path, limits)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def delete_track(track_id: str) -> None:
    with _LOCK:
        removed = next((track for track in _load_index() if track.id == track_id), None)
        if removed is None:
            raise NotFoundError("That music track is no longer in the library.")

    # Delete the file first. If a player still has it open, Windows refuses the
    # unlink; reporting that is far better than dropping the index entry and
    # leaving an orphaned, undeletable file behind.
    try:
        (MUSIC_DIR / removed.stored_name).unlink(missing_ok=True)
    except OSError as exc:
        raise MusicError(
            "That track is still open, so it could not be removed.",
            hint="Stop any preview that is playing, then try removing it again.",
        ) from exc

    with _LOCK:
        _save_index([track for track in _load_index() if track.id != track_id])
    log.info("Removed music track %s.", track_id)


def library_usage() -> dict:
    tracks = _load_index()
    total = sum(track.size for track in tracks)
    return {"count": len(tracks), "bytes": total, "size_label": human_size(total)}


def validate_upload_size(data: bytes, limits: dict) -> None:
    max_mb = int(limits.get("max_audio_mb", 25))
    if not data:
        raise ValidationError("The music file is empty.", field="music")
    if len(data) > max_mb * 1024 * 1024:
        raise ValidationError(f"The music file is larger than {max_mb} MB.", field="music",
                              hint="Use a shorter track or a lower bitrate MP3.")
