"""Scene timing: one source of truth for video and audio.

Every scene has an exact start time. Video crossfades overlap neighbouring
scenes, so ``start`` for scene *i* is ``sum(durations[:i]) - i * transition``.
Narration clips are placed at those same start times (plus a small lead-in),
which is what keeps speech aligned with the picture instead of drifting.

Scene duration is stretched to fit narration plus a padding tail, so a sentence
is never cut off mid-word by a transition.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from services.ffmpeg_tools import snap_to_frame

TITLE = "title"
PHOTO = "photo"
CTA = "cta"


@dataclasses.dataclass
class Scene:
    index: int
    kind: str
    caption: str
    narration: str
    duration: float
    start: float
    image_path: Path | None = None
    narration_path: Path | None = None
    narration_duration: float = 0.0
    narration_offset: float = 0.0

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclasses.dataclass
class Timeline:
    scenes: list[Scene]
    transition: float
    fps: int
    width: int
    height: int
    warnings: list[str] = dataclasses.field(default_factory=list)

    @property
    def total(self) -> float:
        if not self.scenes:
            return 0.0
        return round(self.scenes[-1].end, 3)

    @property
    def photo_scenes(self) -> list[Scene]:
        return [scene for scene in self.scenes if scene.kind == PHOTO]


def build_timeline(
    script: dict[str, Any],
    image_paths: list[Path],
    narration_durations: list[float] | None = None,
    narration_paths: list[Path | None] | None = None,
    *,
    defaults: dict,
    lead_in: float = 0.25,
    padding: float = 0.45,
) -> Timeline:
    """Assemble scenes from a validated script and the ordered photo list."""
    video_cfg = defaults["video"]
    transition = float(video_cfg["transition"])
    fps = int(video_cfg["fps"])
    width = int(video_cfg["width"])
    height = int(video_cfg["height"])

    captions = [str(scene.get("caption", "")).strip()
                for scene in script.get("scenes", []) if isinstance(scene, dict)]
    narrations = [str(scene.get("narration", "")).strip()
                  for scene in script.get("scenes", []) if isinstance(scene, dict)]

    # Pad/truncate defensively: a mismatched script must never break rendering.
    while len(captions) < len(image_paths):
        captions.append("A closer look")
    while len(narrations) < len(image_paths):
        narrations.append("")

    specs: list[dict[str, Any]] = [
        {
            "kind": TITLE,
            "caption": str(script.get("headline", "")).strip(),
            "narration": str(script.get("intro_narration", "")).strip(),
            "base": float(video_cfg["title_duration"]),
            "image": image_paths[0] if image_paths else None,
        }
    ]
    for index, image in enumerate(image_paths):
        specs.append({
            "kind": PHOTO,
            "caption": captions[index],
            "narration": narrations[index],
            "base": float(video_cfg["photo_duration"]),
            "image": image,
        })
    specs.append({
        "kind": CTA,
        "caption": str(script.get("cta_headline", "")).strip(),
        "narration": str(script.get("cta_narration", "")).strip(),
        "base": float(video_cfg["cta_duration"]),
        "image": image_paths[-1] if image_paths else None,
    })

    warnings: list[str] = []
    max_scene = float(video_cfg["max_scene_duration"])
    min_scene = float(video_cfg["min_duration"])

    scenes: list[Scene] = []
    for index, spec in enumerate(specs):
        narration_duration = 0.0
        narration_path = None
        if narration_durations and index < len(narration_durations):
            narration_duration = float(narration_durations[index] or 0.0)
        if narration_paths and index < len(narration_paths):
            narration_path = narration_paths[index]

        duration = max(min_scene, spec["base"])
        offset = min(lead_in, max(0.05, duration * 0.1))
        if narration_duration > 0:
            needed = narration_duration + padding + offset
            if needed > duration:
                duration = needed
            if narration_duration + offset > max_scene + 0.01:
                warnings.append(
                    f"Scene {index + 1} narration is {narration_duration:.1f}s, longer than the "
                    f"{max_scene:.1f}s guideline; the scene was extended to keep speech intact."
                )

        duration = snap_to_frame(duration, fps)

        scenes.append(Scene(
            index=index,
            kind=spec["kind"],
            caption=spec["caption"],
            narration=spec["narration"],
            duration=round(duration, 3),
            start=0.0,
            image_path=spec["image"],
            narration_path=narration_path,
            narration_duration=round(narration_duration, 3),
            narration_offset=round(offset, 3),
        ))

    # Stretch short timelines toward the target minimum, without touching speech.
    prelim_total = sum(scene.duration for scene in scenes) - transition * (len(scenes) - 1)
    target_min = float(video_cfg["target_min_total"])
    if prelim_total < target_min and len(scenes) > 1:
        shortfall = target_min - prelim_total
        stretchable = [s for s in scenes if s.narration_duration == 0]
        if stretchable:
            per_scene = min(1.2, shortfall / len(stretchable))
            for scene in stretchable:
                scene.duration = round(snap_to_frame(scene.duration + per_scene, fps), 3)

    cursor = 0.0
    for index, scene in enumerate(scenes):
        scene.start = round(cursor, 3)
        cursor += scene.duration - transition

    total = round(scenes[-1].end, 3)
    hard_max = float(video_cfg["hard_max_total"])
    if total > hard_max:
        warnings.append(
            f"Video is {total:.1f}s, above the {hard_max:.0f}s target. "
            "Fewer photos or shorter narration would bring it down."
        )

    return Timeline(scenes=scenes, transition=transition, fps=fps, width=width,
                    height=height, warnings=warnings)
