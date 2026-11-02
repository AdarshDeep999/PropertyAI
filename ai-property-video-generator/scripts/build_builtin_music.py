"""Build the three background soundtracks that ship with the app.

These are original recordings synthesised here, so the app can offer music
without licensing anything and without calling a music API. Each track is a
seamless 48 s loop (the renderer repeats it with ``-stream_loop`` and trims to
the video length), encoded to AAC.

Run once after checkout:

    python scripts/build_builtin_music.py

Nothing depends on this script at runtime; the encoded *.m4a files in
``assets/bgm`` are the deliverable.
"""

from __future__ import annotations

import math
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import ffmpeg_tools  # noqa: E402
from services.storage import BGM_DIR  # noqa: E402

SAMPLE_RATE = 44100
CHUNK = 4.0  # seconds per chord
CYCLES = 3
TABLE_SIZE = 8192
_TABLE = [math.sin(2.0 * math.pi * i / TABLE_SIZE) for i in range(TABLE_SIZE)]
_MASK = TABLE_SIZE - 1


def midi_to_hz(note: float) -> float:
    return 440.0 * (2.0 ** ((note - 69.0) / 12.0))


class Mix:
    """Two float buffers, just wide enough for a stereo bed."""

    def __init__(self, seconds: float) -> None:
        self.length = int(round(seconds * SAMPLE_RATE))
        self.left = [0.0] * self.length
        self.right = [0.0] * self.length

    def note(
        self,
        start: float,
        duration: float,
        frequency: float,
        amplitude: float,
        *,
        attack: float = 0.01,
        decay: float = 3.0,
        release: float = 0.35,
        pan: float = 0.0,
        partials: tuple[tuple[float, float], ...] = ((1.0, 1.0),),
        detune: float = 0.0,
    ) -> None:
        first = int(round(start * SAMPLE_RATE))
        count = int(round(duration * SAMPLE_RATE))
        if first >= self.length or count <= 0:
            return
        count = min(count, self.length - first)
        attack_samples = max(1, int(attack * SAMPLE_RATE))
        release_samples = max(1, int(min(release, duration) * SAMPLE_RATE))
        sustain_end = max(0, count - release_samples)

        left_gain = max(0.0, min(1.0, 0.5 * (1.0 - pan)))
        right_gain = max(0.0, min(1.0, 0.5 * (1.0 + pan)))
        increments = [
            (2.0 * math.pi * frequency * multiple * (1.0 + detune) / SAMPLE_RATE,
             weight * amplitude)
            for multiple, weight in partials
        ]

        left = self.left
        right = self.right
        table = _TABLE
        mask = _MASK
        two_pi = 2.0 * math.pi
        phases = [0.0] * len(increments)

        for index in range(count):
            position = first + index
            environment = math.exp(-decay * index / SAMPLE_RATE)
            if index < attack_samples:
                environment *= index / attack_samples
            if index >= sustain_end:
                environment *= max(0.0, (count - index) / release_samples)
            if environment <= 0.0:
                continue

            value = 0.0
            for slot, (step, weight) in enumerate(increments):
                phases[slot] += step
                value += weight * table[int(phases[slot] * TABLE_SIZE / two_pi) & mask]
            value *= environment
            left[position] += value * left_gain * 2.0
            right[position] += value * right_gain * 2.0

    def finish(self, path: Path, peak: float = 0.62) -> Path:
        loudest = 0.0
        for left, right in zip(self.left, self.right):
            loudest = max(loudest, abs(left), abs(right))
        scale = (peak / loudest) if loudest > 0 else 1.0

        fade = int(0.12 * SAMPLE_RATE)
        frames = bytearray()
        for index, (left, right) in enumerate(zip(self.left, self.right)):
            gain = scale
            if index < fade:
                gain *= index / fade
            tail = self.length - index - 1
            if tail < fade:
                gain *= tail / fade
            for sample in (left * gain, right * gain):
                value = int(max(-1.0, min(1.0, sample)) * 32767)
                frames += int(value).to_bytes(2, "little", signed=True)

        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(bytes(frames))
        return path


def _progression(root_octave: int, chords: list[list[int]]) -> list[list[int]]:
    return [[note + 12 * root_octave for note in chord] for chord in chords]


def build_morning_light(total: float) -> Mix:
    """Warm, unhurried: soft pads, a piano-ish arpeggio, gentle bass."""
    chords = _progression(4, [
        [0, 4, 7, 11, 14],    # Cmaj9
        [-3, 0, 4, 7],        # Am7
        [-7, -3, 0, 4],       # Fmaj7
        [-5, -1, 2, 4],       # G6
    ])
    mix = Mix(total)
    step = CHUNK / len(chords)
    pattern = [0, 2, 1, 3, 2]
    for cycle in range(CYCLES):
        for chord_index, chord in enumerate(chords):
            start = cycle * CHUNK * len(chords) + chord_index * CHUNK
            for position, note in enumerate(chord):
                mix.note(start, CHUNK + 0.6, midi_to_hz(note + 12), 0.030,
                         attack=1.1, decay=0.55, release=1.4,
                         pan=-0.5 + position * 0.3, detune=0.0015)
            mix.note(start, CHUNK * 0.9, midi_to_hz(chord[0] - 24), 0.11,
                     attack=0.05, decay=0.9, release=0.8)
            for beat in range(4):
                note = chord[pattern[(chord_index + beat) % len(pattern)]] + 12
                mix.note(start + beat * step / 4.0, 2.4, midi_to_hz(note), 0.055,
                         attack=0.006, decay=4.2, release=0.6,
                         pan=0.35 if beat % 2 else -0.3,
                         partials=((1.0, 1.0), (2.0, 0.32), (3.0, 0.12)))
    return mix


def build_golden_hour(total: float) -> Mix:
    """Slow and cinematic: low pads, sparse high bells."""
    chords = _progression(3, [
        [-3, 0, 4, 7, 10],    # Am9
        [-8, -3, 0, 4],       # Fmaj7
        [-5, -1, 2, 4, 7],    # Cmaj7
        [-10, -5, -1, 2],     # Gsus2
    ])
    mix = Mix(total)
    step = CHUNK / len(chords)
    for cycle in range(CYCLES):
        for chord_index, chord in enumerate(chords):
            start = cycle * CHUNK * len(chords) + chord_index * CHUNK
            for position, note in enumerate(chord):
                mix.note(start, CHUNK + 1.2, midi_to_hz(note + 12), 0.034,
                         attack=1.6, decay=0.35, release=1.8,
                         pan=-0.6 + position * 0.35, detune=0.002)
            mix.note(start, CHUNK, midi_to_hz(chord[0] - 12), 0.12,
                     attack=0.35, decay=0.55, release=1.2)
            for beat in (0, 2.5):
                note = chord[(chord_index + int(beat)) % len(chord)] + 24
                mix.note(start + beat * step / 4.0, 3.0, midi_to_hz(note), 0.030,
                         attack=0.01, decay=2.6, release=1.0, pan=0.4,
                         partials=((1.0, 1.0), (2.0, 0.22), (4.05, 0.08)))
    return mix


def build_fresh_start(total: float) -> Mix:
    """Brighter and a touch quicker, for new listings."""
    chords = _progression(4, [
        [0, 4, 7, 12],        # C
        [-3, 0, 4, 7],        # Am
        [-5, -1, 2, 7],       # G/B
        [-7, -3, 0, 4],       # F
    ])
    mix = Mix(total)
    step = CHUNK / len(chords)
    pattern = [0, 1, 2, 3, 2, 1, 3, 2]
    for cycle in range(CYCLES):
        for chord_index, chord in enumerate(chords):
            start = cycle * CHUNK * len(chords) + chord_index * CHUNK
            for position, note in enumerate(chord):
                mix.note(start, CHUNK + 0.4, midi_to_hz(note + 12), 0.026,
                         attack=0.7, decay=0.6, release=1.0,
                         pan=-0.45 + position * 0.3, detune=0.001)
            mix.note(start, CHUNK * 0.85, midi_to_hz(chord[0] - 24), 0.10,
                     attack=0.04, decay=1.1, release=0.6)
            for beat in range(8):
                note = chord[pattern[(chord_index * 2 + beat) % len(pattern)]] + 12
                mix.note(start + beat * step / 8.0, 1.8, midi_to_hz(note), 0.045,
                         attack=0.004, decay=5.0, release=0.4,
                         pan=0.3 if beat % 3 else -0.35,
                         partials=((1.0, 1.0), (2.0, 0.28), (3.0, 0.10)))
    return mix


TRACKS = {
    "morning-light": build_morning_light,
    "golden-hour": build_golden_hour,
    "fresh-start": build_fresh_start,
}


def encode(wav_path: Path, out_path: Path) -> None:
    result = subprocess.run([
        ffmpeg_tools.ffmpeg_path(), "-y", "-v", "error", "-i", str(wav_path),
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out_path),
    ], capture_output=True)
    if result.returncode != 0:
        raise SystemExit((result.stderr or b"").decode("utf-8", "replace")[-800:])


def main() -> None:
    total = CHUNK * 4 * CYCLES  # 48 s loop
    BGM_DIR.mkdir(parents=True, exist_ok=True)
    for name, builder in TRACKS.items():
        print(f"  synthesising {name}...", flush=True)
        mix = builder(total)
        wav_path = BGM_DIR / f"{name}.wav"
        mix.finish(wav_path)
        out_path = BGM_DIR / f"{name}.m4a"
        encode(wav_path, out_path)
        wav_path.unlink(missing_ok=True)
        print(f"  -> {out_path.name} ({out_path.stat().st_size // 1024} KB)", flush=True)


if __name__ == "__main__":
    main()
