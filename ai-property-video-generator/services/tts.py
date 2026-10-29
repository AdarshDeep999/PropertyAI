"""Text-to-speech engines and fallback policy.

Engines (all server-side, keys never reach the browser):

* ``edge``          - free Microsoft Edge neural voices, no key, needs internet
* ``windows_sapi``  - offline Windows System.Speech, no key, robotic
* ``openai``        - paid API, needs key
* ``elevenlabs``    - free tier/paid API, needs key

Fallback rules, implemented literally:

* Transient failures (timeout, connection error, 429, 5xx) are retried once.
* Any remaining failure may use the *explicitly configured* fallback engine.
* The result always reports the engine that actually produced the audio plus
  ``fallback_used``, so the UI can say "narrated with Windows speech because
  Edge could not be reached". Nothing is silently substituted.
* If no fallback is configured (or it also fails) a TTSError is raised with
  ``can_continue_without_narration`` so the user can choose to continue.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Iterable

import requests

from config.providers import TTS_ENGINES
from config.settings import SettingsStore, store as default_store
from services import ffmpeg_tools
from services.errors import (
    AppError,
    DependencyError,
    ProviderError,
    TTSError,
    classify_request_exception,
    error_from_http,
)
from services.logging_setup import get_logger

log = get_logger("tts")

REQUEST_TIMEOUT = 45
MIN_CLIP_BYTES = 800
MIN_CLIP_SECONDS = 0.25


@dataclasses.dataclass
class TTSResult:
    engine: str
    voice: str
    clips: list[Path]
    durations: list[float]
    fallback_used: bool = False
    primary_engine: str | None = None
    primary_error: str | None = None
    warnings: list[str] = dataclasses.field(default_factory=list)

    @property
    def total_seconds(self) -> float:
        return round(sum(self.durations), 3)

    def to_public(self) -> dict:
        return {
            "engine": self.engine,
            "engine_label": TTS_ENGINES.get(self.engine, {}).get("label", self.engine),
            "voice": self.voice,
            "scene_count": len(self.clips),
            "duration": self.total_seconds,
            "fallback_used": self.fallback_used,
            "primary_engine": self.primary_engine,
            "primary_error": self.primary_error,
            "warnings": list(self.warnings),
        }


# --------------------------------------------------------------------------- #
# Engines
# --------------------------------------------------------------------------- #
def _synth_edge(texts: list[str], voice: str, speed: float, out_dir: Path) -> list[Path]:
    try:
        import edge_tts
    except ImportError as exc:
        raise DependencyError("The Edge voice engine is not installed.",
                              hint="Run: python -m pip install -r requirements.txt") from exc

    rate_percent = int(round((float(speed) - 1.0) * 100))
    rate = f"{rate_percent:+d}%"
    clips: list[Path] = []

    async def _one(text: str, dest: Path) -> None:
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(str(dest))

    for index, text in enumerate(texts):
        dest = out_dir / f"narration_{index:02d}.mp3"
        try:
            asyncio.run(_one(text, dest))
        except Exception as exc:
            message = str(exc)
            lowered = message.lower()
            retryable = any(token in lowered for token in
                            ("timeout", "timed out", "connection", "429", "5xx", "temporarily"))
            raise TTSError(
                "Microsoft's voice service could not generate the narration.",
                hint="Check your internet connection, or switch the voice engine in Settings.",
                retryable=retryable,
                log_detail=f"edge-tts: {message[:400]}",
            ) from exc
        clips.append(dest)
    return clips


def _synth_sapi(texts: list[str], voice: str, speed: float, out_dir: Path) -> list[Path]:
    if os.name != "nt":
        raise TTSError("Windows offline speech is only available on Windows.",
                       hint="Choose the Edge engine instead.", retryable=False)
    clips = [out_dir / f"narration_{index:02d}.wav" for index in range(len(texts))]
    rate = max(-10, min(10, int(round((float(speed) - 1.0) * 10))))
    jobs_file = out_dir / "sapi_jobs.json"
    script_file = out_dir / "sapi_jobs.ps1"
    jobs_file.write_text(json.dumps({
        "voice": voice or "",
        "rate": rate,
        "items": [{"text": text, "path": str(path)} for text, path in zip(texts, clips)],
    }, ensure_ascii=False), encoding="utf-8")

    # Text is passed through JSON, never interpolated into PowerShell source.
    script_file.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "Add-Type -AssemblyName System.Speech\n"
        f"$job = Get-Content -Raw -LiteralPath '{jobs_file}' | ConvertFrom-Json\n"
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        "if ($job.voice) { try { $synth.SelectVoice($job.voice) } catch { } }\n"
        "$synth.Rate = [int]$job.rate\n"
        "foreach ($item in $job.items) {\n"
        "  $synth.SetOutputToWaveFile($item.path)\n"
        "  $synth.Speak([string]$item.text)\n"
        "  $synth.SetOutputToNull()\n"
        "}\n"
        "$synth.Dispose()\n",
        encoding="utf-8",
    )

    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(script_file)],
            capture_output=True, text=True, timeout=30 + 25 * len(texts),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise TTSError("Windows speech took too long and was stopped.", retryable=True,
                       log_detail="SAPI timeout") from exc
    except OSError as exc:
        raise TTSError("Windows speech could not be started.", retryable=False,
                       log_detail=f"SAPI spawn failed: {exc}") from exc

    if result.returncode != 0:
        raise TTSError(
            "Windows speech could not read the script.",
            hint="Choose a different voice in Settings, or use the Edge engine.",
            retryable=False,
            log_detail=f"SAPI rc={result.returncode}: {(result.stderr or '')[:400]}",
        )
    return clips


def _post_audio(url: str, *, headers: dict, payload: dict, provider: str,
                timeout: int = REQUEST_TIMEOUT) -> bytes:
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except Exception as exc:
        raise classify_request_exception(provider, exc) from exc
    if response.status_code >= 400:
        raise error_from_http(provider, response.status_code, response.text[:600])
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "json" in content_type or "text/html" in content_type:
        raise TTSError(f"{provider} returned a non-audio response.",
                       retryable=True,
                       log_detail=f"{provider} content-type={content_type}")
    if not response.content:
        raise TTSError(f"{provider} returned an empty audio response.", retryable=True)
    return response.content


def _synth_openai(texts: list[str], voice: str, speed: float, out_dir: Path,
                  store: SettingsStore) -> list[Path]:
    key = store.get_secret("openai_tts")
    if not key:
        raise TTSError("No OpenAI key is saved for text-to-speech.", retryable=False,
                       hint="Add one in Settings, or choose another engine.")
    model = store.public()["tts"].get("model") or TTS_ENGINES["openai"]["default_model"]
    clips = []
    for index, text in enumerate(texts):
        data = _post_audio(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            payload={"model": model, "voice": voice, "input": text, "response_format": "mp3",
                     "speed": max(0.25, min(4.0, float(speed)))},
            provider="OpenAI text-to-speech",
        )
        dest = out_dir / f"narration_{index:02d}.mp3"
        dest.write_bytes(data)
        clips.append(dest)
    return clips


def _synth_elevenlabs(texts: list[str], voice: str, speed: float, out_dir: Path,
                      store: SettingsStore) -> list[Path]:
    key = store.get_secret("elevenlabs")
    if not key:
        raise TTSError("No ElevenLabs key is saved.", retryable=False,
                       hint="Add one in Settings, or choose another engine.")
    voice_id = voice or TTS_ENGINES["elevenlabs"]["default_voice"]
    clips = []
    for index, text in enumerate(texts):
        data = _post_audio(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": key, "Content-Type": "application/json",
                     "Accept": "audio/mpeg"},
            payload={"text": text, "model_id": "eleven_multilingual_v2",
                     "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            provider="ElevenLabs",
        )
        dest = out_dir / f"narration_{index:02d}.mp3"
        dest.write_bytes(data)
        clips.append(dest)
    return clips


def _synthesise(engine: str, texts: list[str], voice: str, speed: float, out_dir: Path,
                store: SettingsStore) -> list[Path]:
    if engine == "edge":
        return _synth_edge(texts, voice, speed, out_dir)
    if engine == "windows_sapi":
        return _synth_sapi(texts, voice, speed, out_dir)
    if engine == "openai":
        return _synth_openai(texts, voice, speed, out_dir, store)
    if engine == "elevenlabs":
        return _synth_elevenlabs(texts, voice, speed, out_dir, store)
    raise TTSError(f"Unknown voice engine '{engine}'.", retryable=False)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _validate_clips(clips: Iterable[Path]) -> list[float]:
    durations: list[float] = []
    for clip in clips:
        if not clip.is_file():
            raise TTSError("The voice engine did not produce an audio file.",
                           retryable=True, log_detail=f"missing clip: {clip}")
        size = clip.stat().st_size
        if size < MIN_CLIP_BYTES:
            raise TTSError("The voice engine returned audio that was too short.",
                           retryable=True, log_detail=f"tiny clip: {clip} ({size} bytes)")
        try:
            info = ffmpeg_tools.probe(clip)
        except AppError as exc:
            raise TTSError("The generated narration could not be read.",
                           retryable=True, log_detail=exc.log_detail or exc.message) from exc
        if not info.has_audio or info.duration < MIN_CLIP_SECONDS:
            raise TTSError("The voice engine returned an unreadable audio file.",
                           retryable=True,
                           log_detail=f"bad clip: {clip} audio={info.has_audio} dur={info.duration}")
        durations.append(round(info.duration, 3))
    return durations


def _attempt(engine: str, texts: list[str], voice: str, speed: float, out_dir: Path,
             store: SettingsStore, retries: int = 1) -> tuple[list[Path], list[float]]:
    last_error: AppError | None = None
    for attempt in range(retries + 1):
        try:
            clips = _synthesise(engine, texts, voice, speed, out_dir, store)
            durations = _validate_clips(clips)
            return clips, durations
        except AppError as exc:
            last_error = exc
            log.warning("%s attempt %d failed: %s", engine, attempt + 1,
                        exc.log_detail or exc.message)
            if not exc.retryable or attempt >= retries:
                break
            time.sleep(0.8)
    assert last_error is not None
    raise last_error


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def synthesize(texts: list[str], out_dir: Path, *,
               settings_store: SettingsStore | None = None,
               progress: Callable[[int, int], None] | None = None) -> TTSResult:
    """Synthesise one clip per scene, honouring the configured fallback engine."""
    store = settings_store or default_store
    config = store.public()["tts"]
    engine = config.get("engine") or "edge"
    voice = config.get("voice") or TTS_ENGINES.get(engine, {}).get("default_voice", "")
    speed = float(config.get("speed") or 1.0)
    fallback = config.get("fallback_engine") or "none"
    fallback_voice = config.get("fallback_voice") or TTS_ENGINES.get(fallback, {}).get(
        "default_voice", "")

    clean_texts = [" ".join(str(text).split()) for text in texts]
    if not any(clean_texts):
        raise TTSError("There is no narration text to speak.", retryable=False)

    out_dir.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(0, len(clean_texts))

    try:
        clips, durations = _attempt(engine, clean_texts, voice, speed, out_dir, store)
        if progress:
            progress(len(clean_texts), len(clean_texts))
        return TTSResult(engine=engine, voice=voice, clips=clips, durations=durations,
                         fallback_used=False, primary_engine=engine)
    except AppError as primary_error:
        log.warning("Primary voice engine '%s' failed: %s", engine,
                    primary_error.log_detail or primary_error.message)
        if not fallback or fallback == "none" or fallback == engine:
            raise TTSError(
                primary_error.message,
                hint=(primary_error.hint or "") +
                     (" You can continue without narration or try again later."),
                retryable=bool(primary_error.retryable),
                log_detail=primary_error.log_detail,
                details={"can_continue_without_narration": True,
                         "primary_engine": engine,
                         "primary_code": primary_error.code},
            ) from primary_error

        if TTS_ENGINES.get(fallback, {}).get("requires_key"):
            log.info("Trying configured fallback engine '%s'.", fallback)
        try:
            clips, durations = _attempt(fallback, clean_texts, fallback_voice, speed,
                                        out_dir, store)
        except AppError as fallback_error:
            raise TTSError(
                "The voice-over could not be generated.",
                hint="Try the other voice engine in Settings, retry later, or continue "
                     "without narration.",
                retryable=bool(fallback_error.retryable),
                log_detail=(f"primary {engine}: {primary_error.message} | "
                            f"fallback {fallback}: {fallback_error.message}"),
                details={
                    "can_continue_without_narration": True,
                    "primary_engine": engine,
                    "primary_error": primary_error.message,
                    "fallback_engine": fallback,
                    "fallback_error": fallback_error.message,
                },
            ) from fallback_error

        if progress:
            progress(len(clean_texts), len(clean_texts))
        return TTSResult(
            engine=fallback, voice=fallback_voice, clips=clips, durations=durations,
            fallback_used=True, primary_engine=engine,
            primary_error=primary_error.message,
            warnings=[f"Narration used the fallback engine ({TTS_ENGINES.get(fallback, {}).get('label', fallback)}) "
                      f"because {engine} failed: {primary_error.message}"],
        )


def test_engine(engine: str, voice: str | None = None, *,
                settings_store: SettingsStore | None = None) -> dict:
    """Real synthesis of one short phrase. Never reports success without audio."""
    store = settings_store or default_store
    meta = TTS_ENGINES.get(engine)
    if not meta:
        return {"ok": False, "code": "unsupported_choice",
                "message": f"Unknown voice engine '{engine}'."}
    voice = voice or meta["default_voice"]
    with tempfile.TemporaryDirectory(prefix="apvg-tts-test-") as tmp:
        try:
            clips, durations = _attempt(engine, ["Testing the voice engine."], voice, 1.0,
                                        Path(tmp), store, retries=0)
            return {"ok": True, "code": "connected",
                    "message": f"{meta['label']} produced {len(clips)} audio clip "
                               f"({durations[0]:.2f}s) with voice '{voice}'."}
        except AppError as exc:
            return {"ok": False, "code": exc.code, "message": exc.message,
                    "hint": exc.hint, "retryable": exc.retryable}
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("TTS test crashed")
            return {"ok": False, "code": "tts_failed",
                    "message": "The voice engine could not be tested.",
                    "hint": str(exc)[:200]}


def preview(text: str, out_dir: Path, *,
            settings_store: SettingsStore | None = None) -> tuple[Path, TTSResult]:
    """Generate a single preview clip and return its path."""
    text = " ".join((text or "").split())
    if not text:
        raise TTSError("There is no text to speak.", retryable=False)
    if len(text) > 600:
        text = text[:597].rsplit(" ", 1)[0] + "..."
    result = synthesize([text], out_dir, settings_store=settings_store)
    return result.clips[0], result
