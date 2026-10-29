"""Audio assembly: narration placement, music fitting and sidechain ducking."""

from __future__ import annotations

import pytest

from services import audio, ffmpeg_tools
from services.errors import RenderError


def test_narration_is_placed_at_its_offset(tmp_path, make_wav):
    clip = make_wav("line.wav", seconds=1.0, freq=440)
    out = tmp_path / "narration.wav"
    audio.build_narration_track([(clip, 2.0)], total_seconds=5.0, out_wav=out)

    info = ffmpeg_tools.probe(out)
    assert abs(info.duration - 5.0) < 0.05
    quiet, loud = audio.rms_windows(out, [(0.2, 1.5), (2.2, 2.8)])
    assert quiet < 50
    assert loud > 500


def test_narration_track_length_is_exact(tmp_path, make_wav):
    clips = [(make_wav("a.wav", seconds=0.5), 0.0), (make_wav("b.wav", seconds=0.5), 3.0)]
    out = tmp_path / "narration.wav"
    audio.build_narration_track(clips, total_seconds=8.0, out_wav=out)
    assert abs(ffmpeg_tools.probe(out).duration - 8.0) < 0.05


def test_clip_beyond_the_end_is_clipped_not_crashing(tmp_path, make_wav):
    clip = make_wav("long.wav", seconds=4.0)
    out = tmp_path / "narration.wav"
    audio.build_narration_track([(clip, 3.0)], total_seconds=4.0, out_wav=out)
    assert out.is_file()


def test_missing_clip_raises(tmp_path):
    out = tmp_path / "narration.wav"
    with pytest.raises(RenderError):
        audio.build_narration_track([(tmp_path / "missing.wav", 0.0)], 5.0, out)


def test_music_is_looped_and_faded(tmp_path, make_wav):
    source = make_wav("short.wav", seconds=2.0, freq=180)
    out = tmp_path / "music.wav"
    audio.prepare_music(source, total_seconds=6.0, volume=0.5, fade_in=1.0,
                        fade_out=1.0, out_wav=out)
    assert abs(ffmpeg_tools.probe(out).duration - 6.0) < 0.1
    faded_in, middle, faded_out = audio.rms_windows(out, [(0.0, 0.15), (2.5, 3.5), (5.85, 6.0)])
    assert faded_in < middle * 0.5
    assert faded_out < middle * 0.5


def test_ducking_lowers_music_under_narration(tmp_path, make_wav):
    music = make_wav("music.wav", seconds=6.0, freq=200, amplitude=9000)
    narration_clip = make_wav("voice.wav", seconds=2.0, freq=700, amplitude=12000)
    narration = tmp_path / "narration.wav"
    audio.build_narration_track([(narration_clip, 2.0)], 6.0, narration)

    ducked = tmp_path / "ducked.wav"
    audio.duck_music(music, narration, ducked, 6.0, ratio=12, threshold=0.02,
                     attack_ms=10, release_ms=200)

    gap, speech = audio.rms_windows(ducked, [(0.2, 1.8), (2.4, 3.8)])
    assert speech < gap * 0.75, f"expected ducking, gap={gap:.0f} speech={speech:.0f}"


def test_mix_without_audio_returns_none(tmp_path):
    result = audio.mix_tracks(None, None, tmp_path / "mix.wav", 5.0)
    assert result is None


def test_mix_narration_only(tmp_path, make_wav):
    narration = tmp_path / "narration.wav"
    audio.build_narration_track([(make_wav("v.wav", seconds=1.0), 1.0)], 4.0, narration)
    out = audio.mix_tracks(narration, None, tmp_path / "mix.wav", 4.0)
    assert out and out.is_file()
    info = ffmpeg_tools.probe(out)
    assert info.has_audio and abs(info.duration - 4.0) < 0.2


def test_mix_narration_and_music(tmp_path, make_wav):
    narration = tmp_path / "narration.wav"
    audio.build_narration_track([(make_wav("v.wav", seconds=1.0), 1.0)], 5.0, narration)
    music = make_wav("m.wav", seconds=8.0, freq=160)
    out = audio.mix_tracks(narration, music, tmp_path / "mix.wav", 5.0,
                           volume=0.3, music_src=music)
    assert out and out.is_file()
    info = ffmpeg_tools.probe(out)
    assert info.has_audio and abs(info.duration - 5.0) < 0.25


def test_music_only_mix(tmp_path, make_wav):
    music = make_wav("m.wav", seconds=3.0, freq=200)
    out = audio.mix_tracks(None, music, tmp_path / "mix.wav", 6.0, volume=0.4,
                           music_src=music)
    assert out and out.is_file()
    assert abs(ffmpeg_tools.probe(out).duration - 6.0) < 0.25


def test_corrupt_music_raises(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"this is not audio")
    with pytest.raises(RenderError):
        audio.prepare_music(bad, 5.0, 0.3, 1.0, 1.0, tmp_path / "out.wav")
