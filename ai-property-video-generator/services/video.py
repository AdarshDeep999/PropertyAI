"""Video composition and rendering.

Pipeline (deterministic; no MoviePy):

1. For each scene, compose two 3840×2160 layers with Pillow: a photo background
   and a transparent text/scrim overlay.
2. Encode the background with a slow Ken Burns move (`zoompan`) and composite the
   static overlay on top. Text therefore never moves, never gets cropped by the
   zoom, and stays at the same safe margins in every scene.
3. Join the clips with `xfade` crossfades and fade from/to black.
4. Mux the mixed audio (narration and/or user music) as AAC.
5. Verify with FFprobe *and* a full decode pass before declaring success.

Composing at 4K and delivering 1080p gives the zoom room to move without
upscaling artefacts.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from services import audio as audio_tools
from services import ffmpeg_tools
from services import images as image_tools
from services.errors import (
    CancelledError,
    DiskSpaceError,
    RenderError,
)
from services.logging_setup import get_logger
from services.timeline import CTA, PHOTO, TITLE, Scene, Timeline, build_timeline
from services.validation import PropertyFacts

log = get_logger("video")

CANVAS = (3840, 2160)  # 2× the 1080p delivery size
INK = (9, 14, 24)
ACCENT = (216, 166, 87)
WHITE = (255, 255, 255, 255)
MUTED = (214, 219, 228, 255)
SHADOW = (0, 0, 0, 170)
MARGIN_RATIO = 0.05  # 192 px at 4K: ~96 px at 1080p


# --------------------------------------------------------------------------- #
# Fonts and text helpers
# --------------------------------------------------------------------------- #
_FONT_FILES = {
    "bold": ("segoeuib.ttf", "seguibl.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"),
    "regular": ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"),
}
_LINUX_FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu")


def _font_path(weight: str) -> str | None:
    roots = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    roots += [Path(directory) for directory in _LINUX_FONT_DIRS]
    for root in roots:
        for name in _FONT_FILES.get(weight, _FONT_FILES["regular"]):
            candidate = root / name
            if candidate.is_file():
                return str(candidate)
    return None


_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(weight: str, size: int):
    size = max(8, int(size))
    key = (weight, size)
    if key in _font_cache:
        return _font_cache[key]
    path = _font_path(weight)
    try:
        font = ImageFont.truetype(path, size) if path else ImageFont.load_default(size=size)
    except OSError:
        font = ImageFont.load_default(size=size)
    _font_cache[key] = font
    return font


def _clean(value) -> str:
    """Never let None/null/placeholder text reach a frame."""
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return "" if text.lower() in {"none", "null", "undefined", "nan", "n/a"} else text


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _fit_block(draw: ImageDraw.ImageDraw, text: str, weight: str, max_width: int,
               max_lines: int, start_size: int, min_size: int):
    """Shrink type until the text wraps into at most ``max_lines`` lines."""
    size = start_size
    while size > min_size:
        font = _font(weight, size)
        lines = _wrap(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return font, lines
        size -= max(2, int(start_size * 0.04))
    font = _font(weight, min_size)
    return font, _wrap(draw, text, font, max_width)[:max_lines]


def _text_height(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    if not text:
        return 0
    box = draw.textbbox((0, 0), text, font=font)
    return box[3] - box[1]


def _draw_lines(draw: ImageDraw.ImageDraw, xy: tuple[int, int], lines: list[str], font,
                fill, line_height: int) -> int:
    x, y = xy
    for line in lines:
        draw.text((x + 3, y + 3), line, font=font, fill=SHADOW)
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


# --------------------------------------------------------------------------- #
# Scene layers
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class SceneFrame:
    background: Image.Image   # RGB, gets the Ken Burns motion
    overlay: Image.Image      # RGBA, static text and plates

    @property
    def composite(self) -> Image.Image:
        return Image.alpha_composite(self.background.convert("RGBA"), self.overlay).convert("RGB")


def _fallback_background(size: tuple[int, int]) -> Image.Image:
    width, height = size
    base = Image.new("RGB", size, INK)
    draw = ImageDraw.Draw(base)
    for y in range(0, height, 4):
        ratio = y / max(1, height - 1)
        draw.rectangle([0, y, width, y + 4],
                       fill=(int(12 + 18 * ratio), int(18 + 24 * ratio), int(30 + 30 * ratio)))
    return base


def _photo_layer(path: Path | None, size: tuple[int, int], brightness: float = 1.0) -> Image.Image:
    if path and Path(path).is_file():
        try:
            with Image.open(path) as source:
                layer = image_tools.cover_resize(source.convert("RGB"), size)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Could not open photo %s: %s", path, exc)
            layer = _fallback_background(size)
    else:
        layer = _fallback_background(size)
    if brightness != 1.0:
        layer = ImageEnhance.Brightness(layer).enhance(brightness)
    return layer


def _gradient_mask(size: tuple[int, int], top_alpha: int, bottom_alpha: int) -> Image.Image:
    width, height = size
    column = Image.new("L", (1, max(1, height)))
    for y in range(height):
        ratio = y / max(1, height - 1)
        column.putpixel((0, y), int(round(top_alpha + (bottom_alpha - top_alpha) * ratio)))
    return column.resize((width, height), Image.BILINEAR)


def _paste_scrim(base: Image.Image, corner: str, ratio: float, max_alpha: int) -> None:
    """Darken one edge so white text stays legible on any photo."""
    width, height = base.size
    band_height = max(1, int(height * ratio))
    colour = Image.new("RGB", (width, band_height), INK)
    mask = _gradient_mask((width, band_height), 0, max_alpha)
    top = height - band_height if corner == "bottom" else 0
    base.paste(colour, (0, top), mask)


def _new_overlay() -> Image.Image:
    return Image.new("RGBA", CANVAS, (0, 0, 0, 0))


def compose_title(facts: PropertyFacts, script: dict, image_path: Path | None) -> SceneFrame:
    width, height = CANVAS
    background = _photo_layer(image_path, CANVAS, brightness=0.58)
    _paste_scrim(background, "bottom", 0.78, 235)
    _paste_scrim(background, "top", 0.32, 150)

    overlay = _new_overlay()
    draw = ImageDraw.Draw(overlay)
    margin = int(width * MARGIN_RATIO)

    kicker = (_clean(script.get("kicker")) or "Property showcase").upper()
    kicker_font = _font("bold", int(height * 0.023))
    draw.text((margin, margin), kicker, font=kicker_font, fill=ACCENT + (255,))
    rule_y = margin + int(height * 0.023) * 2
    draw.rectangle([margin, rule_y, margin + int(width * 0.045), rule_y + 6],
                   fill=ACCENT + (255,))

    content_width = width - 2 * margin - int(width * 0.02)
    name_font, name_lines = _fit_block(draw, _clean(facts.name), "bold", content_width,
                                       2, int(height * 0.105), int(height * 0.045))
    name_leading = int(name_font.size * 1.06)
    name_height = name_leading * len(name_lines)

    location = _clean(facts.location)
    location_font = _font("regular", int(height * 0.040))
    location_height = _text_height(draw, location, location_font) + 12 if location else 0

    price = _clean(facts.price)
    price_font = _font("bold", int(height * 0.034))
    chip_height = int(price_font.size * 2.0) if price else 0
    chip_width = int(draw.textlength(price, font=price_font) + price_font.size * 1.8) if price else 0

    meta = _clean(facts.meta_line())
    meta_font = _font("regular", int(height * 0.028))
    meta_height = _text_height(draw, meta, meta_font) if meta else 0

    gap = int(height * 0.022)
    block_height = (name_height + (gap + location_height if location else 0)
                    + (gap + chip_height if price else 0)
                    + (gap + meta_height if meta else 0))
    y = height - margin - block_height

    draw.rectangle([margin, y, margin + 10, y + name_height + location_height + 10],
                   fill=ACCENT + (255,))
    text_x = margin + 46
    y = _draw_lines(draw, (text_x, y), name_lines, name_font, WHITE, name_leading)

    if location:
        y += gap
        draw.text((text_x + 3, y + 3), location, font=location_font, fill=SHADOW)
        draw.text((text_x, y), location, font=location_font, fill=MUTED)
        y += location_height

    if price:
        y += gap
        draw.rounded_rectangle([text_x, y, text_x + chip_width, y + chip_height],
                               radius=chip_height // 2, fill=ACCENT + (255,))
        text_y = (y + (chip_height - _text_height(draw, price, price_font)) // 2
                  - price_font.size * 0.12)
        draw.text((text_x + price_font.size * 0.9, text_y), price, font=price_font,
                  fill=INK + (255,))
        y += chip_height

    if meta:
        y += gap
        draw.text((text_x + 3, y + 3), meta, font=meta_font, fill=SHADOW)
        draw.text((text_x, y), meta, font=meta_font, fill=MUTED)

    return SceneFrame(background=background, overlay=overlay)


def compose_photo(facts: PropertyFacts, scene: Scene, position: int,
                  total: int) -> SceneFrame:
    width, height = CANVAS
    background = _photo_layer(scene.image_path, CANVAS, brightness=0.94)
    _paste_scrim(background, "bottom", 0.62, 238)

    overlay = _new_overlay()
    draw = ImageDraw.Draw(overlay)
    margin = int(width * MARGIN_RATIO)

    # Scene counter, top-right, on a soft plate so it reads over any photo.
    counter = f"{position:02d} / {total:02d}"
    counter_font = _font("bold", int(height * 0.022))
    counter_width = draw.textlength(counter, font=counter_font)
    plate = [int(width - margin - counter_width - 44), margin,
             int(width - margin), margin + int(height * 0.052)]
    draw.rounded_rectangle(plate, radius=int(height * 0.026), fill=(0, 0, 0, 145))
    draw.text((plate[0] + 22, plate[1] + int(height * 0.012)), counter,
              font=counter_font, fill=MUTED)

    # Footer line sits on the bottom margin; the caption is placed above it.
    footer = _clean(facts.name)
    footer_font = _font("regular", int(height * 0.020))
    footer_height = _text_height(draw, footer, footer_font) if footer else 0
    caption_bottom = height - margin - (footer_height + int(height * 0.022) if footer
                                        else 0)

    caption = _clean(scene.caption)
    if caption:
        caption_font, caption_lines = _fit_block(draw, caption, "bold",
                                                 int(width * 0.74),
                                                 2, int(height * 0.062), int(height * 0.032))
        leading = int(caption_font.size * 1.10)
        block_height = leading * len(caption_lines)
        y = caption_bottom - block_height
        draw.rectangle([margin, y - int(height * 0.030), margin + int(width * 0.032),
                        y - int(height * 0.030) + 7], fill=ACCENT + (255,))
        _draw_lines(draw, (margin, y), caption_lines, caption_font, WHITE, leading)

    if footer:
        footer_width = draw.textlength(footer, font=footer_font)
        draw.text((width - margin - footer_width, height - margin - footer_height),
                  footer, font=footer_font, fill=MUTED)

    return SceneFrame(background=background, overlay=overlay)


def compose_cta(facts: PropertyFacts, script: dict, image_path: Path | None) -> SceneFrame:
    width, height = CANVAS
    background = _photo_layer(image_path, CANVAS, brightness=0.34)
    _paste_scrim(background, "bottom", 1.0, 225)
    _paste_scrim(background, "top", 0.55, 190)

    overlay = _new_overlay()
    draw = ImageDraw.Draw(overlay)
    margin = int(width * MARGIN_RATIO)
    max_width = width - 2 * margin

    headline = _clean(script.get("cta_headline")) or "Arrange a viewing"
    headline_font, headline_lines = _fit_block(draw, headline, "bold", max_width, 2,
                                               int(height * 0.078), int(height * 0.040))
    headline_leading = int(headline_font.size * 1.08)

    line = _clean(script.get("cta_line"))
    line_font, line_lines = _fit_block(draw, line, "regular", int(max_width * 0.88), 3,
                                       int(height * 0.036), int(height * 0.020))
    line_leading = int(line_font.size * 1.28)

    footer = " · ".join(part for part in (_clean(facts.name), _clean(facts.location)) if part)
    footer_font = _font("regular", int(height * 0.020))

    block = (headline_leading * len(headline_lines) + int(height * 0.045) + 8
             + int(height * 0.045) + line_leading * len(line_lines)
             + (int(height * 0.055) + int(height * 0.024) if footer else 0))
    y = max(margin, (height - block) // 2)

    def centred(lines: list[str], font, leading: int, fill) -> None:
        nonlocal y
        for text in lines:
            text_width = draw.textlength(text, font=font)
            x = (width - text_width) / 2
            draw.text((x + 3, y + 3), text, font=font, fill=SHADOW)
            draw.text((x, y), text, font=font, fill=fill)
            y += leading

    centred(headline_lines, headline_font, headline_leading, WHITE)
    y += int(height * 0.045)
    rule_width = int(width * 0.055)
    draw.rectangle([(width - rule_width) / 2, y, (width + rule_width) / 2, y + 7],
                   fill=ACCENT + (255,))
    y += 8 + int(height * 0.045)
    centred(line_lines, line_font, line_leading, MUTED)

    if footer:
        y += int(height * 0.055)
        centred([footer], footer_font, int(height * 0.024), ACCENT + (255,))

    return SceneFrame(background=background, overlay=overlay)


def compose_scenes(timeline: Timeline, facts: PropertyFacts, script: dict, out_dir: Path,
                   progress: Callable[[str, str], None] | None = None,
                   should_cancel: Callable[[], bool] | None = None) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    photos = timeline.photo_scenes
    total_photos = max(1, len(photos))
    frames: list[dict] = []

    for scene in timeline.scenes:
        if should_cancel and should_cancel():
            raise CancelledError()
        if progress:
            progress("render", f"Composing scene {scene.index + 1} of {len(timeline.scenes)}")
        if scene.kind == TITLE:
            frame = compose_title(facts, script, scene.image_path)
        elif scene.kind == CTA:
            frame = compose_cta(facts, script, scene.image_path)
        else:
            frame = compose_photo(facts, scene, scene.index, total_photos)

        background = out_dir / f"scene_{scene.index:02d}_bg.png"
        overlay = out_dir / f"scene_{scene.index:02d}_fg.png"
        frame.background.save(background, format="PNG")
        frame.overlay.save(overlay, format="PNG")
        frames.append({"background": background, "overlay": overlay,
                       "composite": frame.composite})
    return frames


# --------------------------------------------------------------------------- #
# Clip encoding
# --------------------------------------------------------------------------- #
def _zoom_expression(motion: str) -> str:
    if motion == "out":
        return "if(lte(zoom,1.0),1.12,max(1.001,zoom-0.0011))"
    if motion == "in":
        return "min(zoom+0.0011,1.12)"
    return "1.06"


def render_clip(background: Path, overlay: Path, duration: float, destination: Path, *,
                width: int, height: int, fps: int, motion: str,
                should_cancel: Callable[[], bool] | None = None) -> Path:
    """Ken Burns on the photo, static text on top, encoded as H.264."""
    frames = max(1, int(round(float(duration) * fps)))
    zoom = _zoom_expression(motion)
    graph = (
        f"[0:v]zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s={width}x{height}:fps={fps}[bg];"
        f"[1:v]scale={width}:{height},format=rgba[fg];"
        f"[bg][fg]overlay=0:0:format=auto,format=yuv420p[v]"
    )
    command = [
        ffmpeg_tools.ffmpeg_path(), "-y", "-nostdin", "-v", "error",
        "-loop", "1", "-framerate", str(fps), "-i", str(background),
        "-loop", "1", "-framerate", str(fps), "-i", str(overlay),
        "-filter_complex", graph, "-map", "[v]",
        "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-an", str(destination),
    ]
    result = ffmpeg_tools.run_cancellable(command, should_cancel=should_cancel, timeout=300)
    if result.returncode != 0 or not destination.is_file():
        raise RenderError(
            "A video scene could not be encoded.",
            hint="Retry the generation. If it keeps failing, try fewer photos.",
            log_detail=(result.stderr or "")[-1200:],
        )
    return destination


# --------------------------------------------------------------------------- #
# Final assembly
# --------------------------------------------------------------------------- #
def _mux(clips: list[Path], audio_wav: Path | None, output: Path, timeline: Timeline,
         should_cancel: Callable[[], bool] | None = None) -> None:
    total = timeline.total
    command = [ffmpeg_tools.ffmpeg_path(), "-y", "-nostdin", "-v", "error"]
    for clip in clips:
        command += ["-i", str(clip)]
    if audio_wav:
        command += ["-i", str(audio_wav)]

    steps: list[str] = []
    previous = "[0:v]"
    for index in range(1, len(clips)):
        offset = timeline.scenes[index].start
        target = f"[vx{index}]"
        steps.append(
            f"{previous}[{index}:v]xfade=transition=fade:duration={timeline.transition:.3f}"
            f":offset={offset:.3f}{target}"
        )
        previous = target
    fade_out_start = max(0.0, total - 0.8)
    steps.append(
        f"{previous}fade=t=in:st=0:d=0.6,fade=t=out:st={fade_out_start:.3f}:d=0.8[vout]"
    )
    command += ["-filter_complex", ";".join(steps), "-map", "[vout]"]

    if audio_wav:
        command += ["-map", f"{len(clips)}:a", "-c:a", "aac", "-b:a", "192k",
                    "-ar", "48000", "-ac", "2"]
    command += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-r", str(timeline.fps), "-movflags", "+faststart",
        "-t", f"{total:.3f}", str(output),
    ]
    result = ffmpeg_tools.run_cancellable(command, should_cancel=should_cancel, timeout=900)
    if result.returncode != 0 or not output.is_file():
        raise RenderError(
            "The video could not be assembled.",
            hint="Retry the generation. Your photos and details are still here.",
            log_detail=(result.stderr or "")[-1500:],
        )


def verify_output(path: Path, expected_duration: float, width: int, height: int) -> dict:
    """Refuse to call a render successful unless the file really plays."""
    if not path.is_file():
        raise RenderError("The rendered video file is missing.",
                          log_detail=f"missing output: {path}")
    size = path.stat().st_size
    if size < 60_000:
        raise RenderError("The rendered video is suspiciously small, so it was rejected.",
                          hint="Retry the generation.", log_detail=f"size={size} bytes")
    info = ffmpeg_tools.probe(path)
    if not info.has_video:
        raise RenderError("The rendered file has no video stream.",
                          log_detail=f"probe: {info.summary()}")
    if info.video_codec not in ("h264", "avc1"):
        raise RenderError("The rendered video did not use an MP4-compatible codec.",
                          log_detail=f"codec={info.video_codec}")
    if info.width != width or info.height != height:
        raise RenderError("The rendered video has an unexpected size.",
                          log_detail=f"{info.width}x{info.height} != {width}x{height}")
    tolerance = max(1.0, expected_duration * 0.05)
    if abs(info.duration - expected_duration) > tolerance:
        raise RenderError("The rendered video length is wrong.",
                          log_detail=f"duration {info.duration:.2f}s vs expected "
                                     f"{expected_duration:.2f}s")
    if not ffmpeg_tools.decode_check(path):
        raise RenderError("The rendered MP4 could not be fully decoded.",
                          hint="Retry the generation.", log_detail="decode_check failed")
    return {
        "duration": round(info.duration, 3),
        "size_bytes": size,
        "width": info.width,
        "height": info.height,
        "fps": info.fps or 0,
        "video_codec": info.video_codec,
        "audio_codec": info.audio_codec,
        "has_audio": info.has_audio,
        "verified": True,
    }


@dataclasses.dataclass
class RenderRequest:
    facts: PropertyFacts
    script: dict
    images: list[Path]
    work_dir: Path
    output_path: Path
    narration_clips: list[Path] | None = None
    narration_durations: list[float] | None = None
    music_path: Path | None = None
    music_name: str = ""
    music_volume: float = 0.30
    width: int | None = None
    height: int | None = None
    progress: Callable[[str, str], None] | None = None
    should_cancel: Callable[[], bool] | None = None
    defaults: dict | None = None


def render_video(request: RenderRequest) -> dict:
    from config.settings import store  # local import keeps module import cheap

    defaults = request.defaults or _load_defaults()
    video_cfg = defaults["video"]
    limits = defaults["limits"]
    width = int(request.width or video_cfg["width"])
    height = int(request.height or video_cfg["height"])
    fps = int(video_cfg["fps"])

    free_mb = ffmpeg_tools.disk_free_mb(request.work_dir)
    if free_mb < int(limits["min_free_disk_mb"]):
        raise DiskSpaceError(int(limits["min_free_disk_mb"]), free_mb)

    public = store.public()
    timeline = build_timeline(
        request.script, request.images, request.narration_durations,
        request.narration_clips,
        defaults=defaults,
        lead_in=float(defaults["narration"]["lead_in"]),
        padding=float(defaults["narration"]["padding"]),
    )

    scenes_dir = request.work_dir / "scenes"
    clips_dir = request.work_dir / "clips"
    audio_dir = request.work_dir / "audio"
    for directory in (scenes_dir, clips_dir, audio_dir):
        directory.mkdir(parents=True, exist_ok=True)

    def cancelled() -> bool:
        return bool(request.should_cancel and request.should_cancel())

    # 1) layers ------------------------------------------------------------ #
    frames = compose_scenes(timeline, request.facts, request.script, scenes_dir,
                            progress=request.progress,
                            should_cancel=request.should_cancel)

    # 2) clips ------------------------------------------------------------- #
    clip_paths: list[Path] = []
    for index, (scene, frame) in enumerate(zip(timeline.scenes, frames)):
        if cancelled():
            raise CancelledError()
        if request.progress:
            request.progress("render",
                             f"Rendering scene {index + 1} of {len(timeline.scenes)}")
        motion = "in" if index == 0 else ("out" if index == len(timeline.scenes) - 1
                                          else ("out" if index % 2 == 0 else "in"))
        clip_paths.append(render_clip(
            frame["background"], frame["overlay"], scene.duration,
            clips_dir / f"clip_{index:02d}.mp4",
            width=width, height=height, fps=fps, motion=motion,
            should_cancel=request.should_cancel,
        ))

    # 3) audio ------------------------------------------------------------- #
    narration_wav: Path | None = None
    if request.narration_clips and request.narration_durations:
        entries = [
            (scene.narration_path, scene.start + scene.narration_offset)
            for scene in timeline.scenes
            if scene.narration_path and scene.narration_duration > 0
        ]
        if entries:
            narration_wav = audio_tools.build_narration_track(
                entries, timeline.total, audio_dir / "narration.wav"
            )

    music_cfg = defaults["music"]
    mixed_audio: Path | None = None
    if narration_wav or request.music_path:
        if request.progress:
            request.progress("render", "Mixing audio")
        mixed_audio = audio_tools.mix_tracks(
            narration_wav, request.music_path, audio_dir / "mix.wav", timeline.total,
            volume=float(request.music_volume),
            fade_in=float(music_cfg["fade_in"]), fade_out=float(music_cfg["fade_out"]),
            duck_ratio=float(music_cfg["duck_ratio"]),
            duck_threshold=float(music_cfg["duck_threshold"]),
            loudness_i=float(defaults["audio"]["loudness_i"]),
            true_peak=float(defaults["audio"]["true_peak"]),
            music_src=request.music_path,
        )

    # 4) mux --------------------------------------------------------------- #
    if cancelled():
        raise CancelledError()
    if request.progress:
        request.progress("render", "Assembling final MP4")
    request.output_path.parent.mkdir(parents=True, exist_ok=True)
    _mux(clip_paths, mixed_audio, request.output_path, timeline,
         should_cancel=request.should_cancel)

    # 5) verify ------------------------------------------------------------ #
    if request.progress:
        request.progress("verify", "Checking the MP4 plays")
    info = verify_output(request.output_path, timeline.total, width, height)

    poster = request.output_path.with_suffix(".jpg")
    try:
        frames[0]["composite"].resize((1280, 720), Image.LANCZOS).save(
            poster, format="JPEG", quality=86
        )
    except Exception as exc:  # poster is optional polish
        log.warning("Poster generation skipped: %s", exc)
        poster = Path("")

    return {
        **info,
        "path": str(request.output_path),
        "poster_path": str(poster) if poster else "",
        "music_used": bool(request.music_path),
        "music_name": request.music_name,
        "narration_used": bool(narration_wav),
        "timeline": {
            "total": timeline.total,
            "transition": timeline.transition,
            "scenes": [
                {"index": scene.index, "kind": scene.kind, "caption": scene.caption,
                 "duration": scene.duration, "start": scene.start}
                for scene in timeline.scenes
            ],
        },
        "warnings": timeline.warnings,
        "resolution": f"{width}×{height}",
        "tone": public.get("video", {}).get("tone", "warm"),
    }


def _load_defaults() -> dict:
    import json

    from services.storage import CONFIG_DIR

    return json.loads((CONFIG_DIR / "defaults.json").read_text(encoding="utf-8"))
