"""Real FFmpeg integration: these tests actually render and decode an MP4."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import ffmpeg_tools
from services.errors import RenderError
from services.video import RenderRequest, render_video, verify_output

DEFAULTS = json.loads((Path(__file__).resolve().parent.parent / "config" /
                       "defaults.json").read_text(encoding="utf-8"))


@pytest.mark.slow
def test_real_render_produces_a_playable_silent_mp4(tmp_path, make_images, sample_facts,
                                                    script_payload):
    images = make_images(4)
    output = tmp_path / "video" / "sample.mp4"
    info = render_video(RenderRequest(
        facts=sample_facts,
        script=script_payload,
        images=images,
        work_dir=tmp_path / "work",
        output_path=output,
        width=854,
        height=480,
        defaults=DEFAULTS,
    ))

    assert output.is_file()
    assert info["verified"] is True
    assert info["has_audio"] is False
    assert 15 <= info["duration"] <= 45

    probe = ffmpeg_tools.probe(output)
    assert probe.has_video
    assert probe.video_codec == "h264"
    assert (probe.width, probe.height) == (854, 480)
    assert probe.duration > 10
    assert ffmpeg_tools.decode_check(output) is True


@pytest.mark.slow
def test_real_render_with_narration_track(tmp_path, make_images, sample_facts,
                                          script_payload, make_wav):
    images = make_images(4)
    clips = [make_wav(f"n{index}.wav", seconds=1.2, freq=420 + index * 40)
             for index in range(6)]
    output = tmp_path / "video" / "narration.mp4"
    info = render_video(RenderRequest(
        facts=sample_facts,
        script=script_payload,
        images=images,
        work_dir=tmp_path / "work2",
        output_path=output,
        narration_clips=clips,
        narration_durations=[1.2] * 6,
        width=640,
        height=360,
        defaults=DEFAULTS,
    ))
    assert info["has_audio"] is True
    assert info["audio_codec"] == "aac"
    assert ffmpeg_tools.decode_check(output)


def test_verify_rejects_a_tiny_file(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(RenderError):
        verify_output(bad, expected_duration=20.0, width=1920, height=1080)


def test_verify_rejects_missing_file(tmp_path):
    with pytest.raises(RenderError):
        verify_output(tmp_path / "missing.mp4", 20.0, 1920, 1080)


def test_verify_rejects_wrong_duration(tmp_path, make_images, sample_facts,
                                       script_payload):
    """A real MP4 with the wrong length must not be accepted as successful."""
    images = make_images(4)
    output = tmp_path / "short.mp4"
    render_video(RenderRequest(
        facts=sample_facts, script=script_payload, images=images,
        work_dir=tmp_path / "w3", output_path=output,
        width=320, height=180, defaults=DEFAULTS,
    ))
    with pytest.raises(RenderError):
        verify_output(output, expected_duration=999.0, width=320, height=180)
