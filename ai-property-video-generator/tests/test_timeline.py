"""Timeline maths: narration fitting, frame snapping and crossfade offsets."""

from __future__ import annotations

import json
from pathlib import Path

from services.timeline import CTA, PHOTO, TITLE, build_timeline

DEFAULTS = json.loads((Path(__file__).resolve().parent.parent / "config" /
                       "defaults.json").read_text(encoding="utf-8"))


def _images(count=4):
    return [Path(f"image{index}.jpg") for index in range(count)]


def _build(script, count=4, durations=None, paths=None, defaults=None, **kwargs):
    return build_timeline(
        script, _images(count), durations, paths,
        defaults=defaults or DEFAULTS,
        lead_in=DEFAULTS["narration"]["lead_in"],
        padding=DEFAULTS["narration"]["padding"],
        **kwargs,
    )


def test_scene_count_is_photos_plus_two(script_payload):
    timeline = _build(script_payload, 4)
    assert len(timeline.scenes) == 6
    assert timeline.scenes[0].kind == TITLE
    assert timeline.scenes[-1].kind == CTA
    assert [scene.kind for scene in timeline.scenes[1:-1]] == [PHOTO] * 4


def test_starts_account_for_crossfade_overlap(script_payload):
    timeline = _build(script_payload, 4)
    transition = timeline.transition
    for index in range(1, len(timeline.scenes)):
        expected = timeline.scenes[index - 1].start + timeline.scenes[index - 1].duration - transition
        assert round(expected, 3) == round(timeline.scenes[index].start, 3)


def test_total_matches_last_scene_end(script_payload):
    timeline = _build(script_payload, 4)
    assert round(timeline.total, 3) == round(timeline.scenes[-1].end, 3)


def test_durations_snap_to_frames(script_payload):
    timeline = _build(script_payload, 4)
    for scene in timeline.scenes:
        frames = scene.duration * timeline.fps
        assert abs(frames - round(frames)) < 1e-6


def test_narration_extends_the_scene(script_payload):
    durations = [0.0, 7.5, 0.0, 0.0, 0.0, 0.0]
    timeline = _build(script_payload, 4, durations=durations)
    assert timeline.scenes[1].duration >= 7.5 + DEFAULTS["narration"]["padding"]
    assert timeline.scenes[1].narration_duration == 7.5


def test_narration_offset_leaves_a_lead_in(script_payload):
    timeline = _build(script_payload, 4, durations=[2.0] * 6)
    for scene in timeline.scenes:
        assert 0 < scene.narration_offset <= DEFAULTS["narration"]["lead_in"]


def test_short_timeline_is_stretched(script_payload):
    tight = json.loads(json.dumps(DEFAULTS))
    tight["video"].update({"title_duration": 1.0, "photo_duration": 1.0,
                           "cta_duration": 1.0, "min_duration": 1.0,
                           "target_min_total": 20.0})
    timeline = _build(script_payload, 4, defaults=tight)
    natural = 6 * 1.0 - 5 * 0.6
    assert timeline.total > natural
    assert timeline.total <= 20.5


def test_long_narration_warns_but_never_truncates(script_payload):
    durations = [0.0, 20.0, 0.0, 0.0, 0.0, 0.0]
    timeline = _build(script_payload, 4, durations=durations)
    assert timeline.scenes[1].duration >= 20.0
    assert timeline.warnings


def test_script_with_too_few_scenes_is_padded(script_payload):
    short = dict(script_payload)
    short["scenes"] = script_payload["scenes"][:2]
    timeline = _build(short, 4)
    assert len(timeline.scenes) == 6
    assert all(scene.caption for scene in timeline.scenes[1:-1])


def test_no_images_does_not_crash(script_payload):
    timeline = build_timeline(script_payload, [], None, None, defaults=DEFAULTS,
                              lead_in=0.25, padding=0.45)
    assert len(timeline.scenes) == 2  # title + CTA only
    assert timeline.total > 0
