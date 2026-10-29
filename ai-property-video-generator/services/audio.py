"""Audio assembly.

Two deliberate choices keep this reliable and testable:

* The narration track is built in Python as an exact-length 48 kHz stereo WAV,
  with each scene's clip placed at the scene start time. FFmpeg then only has to
  mix two files of identical length instead of juggling dozens of delay filters.
* Ducking uses ``sidechaincompress`` keyed on the narration, which follows the
  voice naturally (including pauses) rather than a hard-coded on/off envelope.

No music is ever generated: every non-empty music path comes from a user file.
"""

from __future__ import annotations

import array
import subprocess
import wave
from pathlib import Path
from typing import Iterable

from services import ffmpeg_tools
from services.errors import RenderError
from services.logging_setup import get_logger

log = get_logger("audio")
SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2  # 16-bit PCM
FADE_SAMPLES = int(0.015 * SAMPLE_RATE)  # 15 ms de-click at clip edges


def _run_bytes(args: list[str], timeout: int = 240) -> bytes:
    try:
        result = subprocess.run(
            args, capture_output=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if __import__("os").name == "nt" else 0,
        )
    except FileNotFoundError as exc:
        raise RenderError("FFmpeg is not available.", log_detail=str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderError("An audio operation timed out.", hint="Try again.",
                          log_detail=f"timeout: {' '.join(args[:3])}") from exc
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace")[-1200:]
        raise RenderError("An audio operation failed.",
                          hint="Check the music file and try again.",
                          log_detail=f"ffmpeg rc={result.returncode}: {detail}")
    return result.stdout


def decode_pcm(path: str | Path, *, sample_rate: int = SAMPLE_RATE,
               channels: int = CHANNELS) -> bytes:
    """Decode any audio file to raw signed-16-bit PCM."""
    return _run_bytes([
        ffmpeg_tools.ffmpeg_path(), "-v", "error", "-i", str(path),
        "-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(channels),
        "-ar", str(sample_rate), "-",
    ])


def build_narration_track(entries: Iterable[tuple[Path, float]], total_seconds: float,
                          out_wav: Path) -> Path:
    """Place each narration clip at its offset in a silent stereo bed of exact length.

    ``entries`` is ``[(clip_path, offset_seconds), ...]``. Overlaps are summed
    with clipping protection, though the timeline never produces overlaps.
    """
    total_samples = max(1, int(round(float(total_seconds) * SAMPLE_RATE)))
    samples = array.array("h", bytes(total_samples * CHANNELS * SAMPLE_WIDTH))

    for clip_path, offset in entries:
        if not Path(clip_path).is_file():
            raise RenderError("A narration clip is missing.",
                              log_detail=f"missing clip {clip_path}")
        pcm = decode_pcm(clip_path)
        clip = array.array("h")
        clip.frombytes(pcm[: len(pcm) - (len(pcm) % SAMPLE_WIDTH)])

        if FADE_SAMPLES * 2 < len(clip):
            for i in range(FADE_SAMPLES):
                gain = i / FADE_SAMPLES
                for channel in range(CHANNELS):
                    idx = i * CHANNELS + channel
                    clip[idx] = int(clip[idx] * gain)
                    back = len(clip) - (FADE_SAMPLES - i) * CHANNELS + channel
                    if 0 <= back < len(clip):
                        clip[back] = int(clip[back] * gain)

        start_frame = max(0, int(round(float(offset) * SAMPLE_RATE)))
        start_index = start_frame * CHANNELS
        limit = min(len(clip), len(samples) - start_index)
        for i in range(limit):
            idx = start_index + i
            mixed = samples[idx] + clip[i]
            samples[idx] = 32767 if mixed > 32767 else (-32768 if mixed < -32768 else mixed)

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(samples.tobytes())
    return out_wav


def prepare_music(src: Path, total_seconds: float, volume: float, fade_in: float,
                  fade_out: float, out_wav: Path) -> Path:
    """Loop/trim the user's track to the video length with fades and user volume."""
    total = max(0.5, float(total_seconds))
    volume = max(0.0, min(1.0, float(volume)))
    fade_out = max(0.0, min(fade_out, total / 2))
    fade_in = max(0.0, min(fade_in, total / 2))
    filters = [
        f"atrim=0:{total:.3f}",
        f"volume={volume:.3f}",
        f"afade=t=in:st=0:d={fade_in:.2f}",
        f"afade=t=out:st={max(0.0, total - fade_out):.3f}:d={fade_out:.2f}",
        f"aformat=sample_fmts=s16:channel_layouts=stereo:sample_rates={SAMPLE_RATE}",
    ]
    filter_str = ",".join(filters)
    result = ffmpeg_tools.run([
        ffmpeg_tools.ffmpeg_path(), "-y", "-stream_loop", "-1", "-i", str(src),
        "-t", f"{total:.3f}", "-vn", "-af", filter_str,
        "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
        str(out_wav),
    ], timeout=240)
    if result.returncode != 0 or not out_wav.is_file():
        raise RenderError(
            "The background music could not be prepared.",
            hint="Try a different track (MP3 or WAV works best).",
            log_detail=(result.stderr or "")[-1000:],
        )
    return out_wav


def duck_music(music_wav: Path, narration_wav: Path, out_wav: Path, total_seconds: float,
               *, ratio: float = 8, threshold: float = 0.03, attack_ms: int = 15,
               release_ms: int = 400) -> Path:
    """Lower the music while narration is present (sidechain compression)."""
    filter_str = (
        f"[0:a][1:a]sidechaincompress=threshold={threshold}:ratio={ratio}:"
        f"attack={attack_ms}:release={release_ms}:makeup=1[duck]"
    )
    result = ffmpeg_tools.run([
        ffmpeg_tools.ffmpeg_path(), "-y", "-i", str(music_wav), "-i", str(narration_wav),
        "-filter_complex", filter_str, "-map", "[duck]", "-t", f"{float(total_seconds):.3f}",
        "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(out_wav),
    ], timeout=240)
    if result.returncode != 0 or not out_wav.is_file():
        raise RenderError("Audio ducking failed.", log_detail=(result.stderr or "")[-1000:])
    return out_wav


def _apply_loudness(src: Path, out_wav: Path, total_seconds: float, *,
                    target_i: float = -16.0, true_peak: float = -1.5) -> Path:
    filters = (
        f"loudnorm=I={target_i}:TP={true_peak}:LRA=11,"
        f"aresample={SAMPLE_RATE},alimiter=limit=0.95"
    )
    result = ffmpeg_tools.run([
        ffmpeg_tools.ffmpeg_path(), "-y", "-i", str(src), "-af", filters,
        "-t", f"{float(total_seconds):.3f}", "-c:a", "pcm_s16le",
        "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(out_wav),
    ], timeout=240)
    if result.returncode != 0 or not out_wav.is_file():
        raise RenderError("Audio loudness processing failed.",
                          log_detail=(result.stderr or "")[-1000:])
    return out_wav


def mix_tracks(narration_wav: Path | None, music_wav: Path | None, out_wav: Path,
               total_seconds: float, *, volume: float = 0.30, fade_in: float = 1.5,
               fade_out: float = 1.8, duck_ratio: float = 8, duck_threshold: float = 0.03,
               loudness_i: float = -16.0, true_peak: float = -1.5,
               music_src: Path | None = None) -> Path | None:
    """Produce the final mixed audio, or None when there is no audio at all."""
    if not narration_wav and not music_wav:
        return None

    work = out_wav.parent
    if narration_wav and music_wav:
        music_bed = prepare_music(music_src or music_wav, total_seconds, volume,
                                  fade_in, fade_out, work / "music_bed.wav")
        ducked = duck_music(music_bed, narration_wav, work / "music_ducked.wav",
                            total_seconds, ratio=duck_ratio, threshold=duck_threshold)
        amixed = work / "audio_mixed.wav"
        result = ffmpeg_tools.run([
            ffmpeg_tools.ffmpeg_path(), "-y", "-i", str(narration_wav), "-i", str(ducked),
            "-filter_complex",
            "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]",
            "-map", "[a]", "-t", f"{float(total_seconds):.3f}",
            "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(amixed),
        ], timeout=240)
        if result.returncode != 0:
            raise RenderError("Mixing narration and music failed.",
                              log_detail=(result.stderr or "")[-1000:])
        return _apply_loudness(amixed, out_wav, total_seconds, target_i=loudness_i,
                               true_peak=true_peak)

    single = narration_wav or music_wav
    assert single is not None
    if music_wav and not narration_wav:
        single = prepare_music(music_src or music_wav, total_seconds, volume,
                               fade_in, fade_out, work / "music_bed.wav")
    return _apply_loudness(single, out_wav, total_seconds, target_i=loudness_i,
                           true_peak=true_peak)


def rms_windows(wav_path: Path, windows: list[tuple[float, float]]) -> list[float]:
    """RMS amplitude per time window. Used to prove ducking actually happens."""
    pcm = decode_pcm(wav_path, channels=1)
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % SAMPLE_WIDTH)])
    results = []
    for start, end in windows:
        first = max(0, int(start * SAMPLE_RATE))
        last = min(len(samples), int(end * SAMPLE_RATE))
        chunk = samples[first:last]
        if not chunk:
            results.append(0.0)
            continue
        total = sum(int(value) * int(value) for value in chunk)
        results.append((total / len(chunk)) ** 0.5)
    return results
