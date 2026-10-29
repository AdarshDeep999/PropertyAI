"""Image handling.

Every upload is fully decoded and re-encoded before use. That does three things:

* a file with an image extension but no valid image data is rejected early;
* EXIF metadata (including GPS coordinates) is dropped;
* all formats converge to sRGB JPEG, which FFmpeg/Pillow handle predictably.

Filenames are chosen by the app (``image01.jpg``), never derived from user input.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from services.errors import ImageError
from services.logging_setup import get_logger

log = get_logger("images")

MAX_STORED_SIDE = 2560  # plenty for 1080p output, keeps render memory sane
THUMB_SIZE = (480, 270)


def _open_verified(data: bytes) -> Image.Image:
    """Decode bytes twice: verify() catches truncation, load() proves decodability."""
    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageError(
            "One of the photos is damaged or is not a real image.",
            hint="Open it on your computer, re-save it as JPG, then upload it again.",
        ) from exc
    return image


def normalise_image(data: bytes, dest: Path, limits: dict) -> dict:
    """Validate, orient, strip metadata, downscale and save as JPEG."""
    image = _open_verified(data)
    width, height = image.size
    min_w = int(limits.get("min_image_width", 400))
    min_h = int(limits.get("min_image_height", 225))
    if width < min_w or height < min_h:
        raise ImageError(
            f"One photo is only {width}×{height} pixels, which is too small.",
            hint=f"Use photos at least {min_w}×{min_h} pixels; 1200×675 or larger looks best.",
        )

    try:
        image = ImageOps.exif_transpose(image)
    except Exception:  # pragma: no cover - malformed EXIF is not fatal
        pass

    if image.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", image.size, (255, 255, 255))
        converted = image.convert("RGBA")
        background.paste(converted, mask=converted.split()[-1])
        image = background
    else:
        image = image.convert("RGB")

    longest = max(image.size)
    if longest > MAX_STORED_SIDE:
        scale = MAX_STORED_SIDE / longest
        image = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            Image.LANCZOS,
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, format="JPEG", quality=92, optimize=True, progressive=False)
    return {"path": str(dest), "width": image.width, "height": image.height,
            "bytes": dest.stat().st_size}


def make_thumbnail(src: Path, dest: Path, size: tuple[int, int] = THUMB_SIZE) -> Path:
    with Image.open(src) as image:
        image = image.convert("RGB")
        thumb = cover_resize(image, size)
        dest.parent.mkdir(parents=True, exist_ok=True)
        thumb.save(dest, format="JPEG", quality=85)
    return dest


def cover_resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale + centre-crop to exactly ``size`` (no stretching, no letterboxing)."""
    target_w, target_h = size
    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return Image.new("RGB", size, (24, 28, 36))
    scale = max(target_w / src_w, target_h / src_h)
    new_size = (max(1, int(round(src_w * scale))), max(1, int(round(src_h * scale))))
    resized = image.resize(new_size, Image.LANCZOS)
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def image_dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size
