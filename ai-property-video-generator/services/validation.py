"""Validation for property details and uploads.

Principle: a missing field is normal, not an error. Only name and location are
required; everything else is optional and rendered only when supplied. Strings
that mean "nothing" (``None``, ``null``, ``nan`` typed by a user or inherited from
a template) are normalised to empty so they can never reach the video.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from services.errors import ImageError, ValidationError
from services.storage import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, extension_of, safe_original_name

_EMPTY_TOKENS = {"", "none", "null", "undefined", "nan", "n/a", "na", "-", "--"}
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")

PROPERTY_TYPES = ["Apartment", "House", "Villa", "Townhouse", "Bungalow", "Studio",
                  "Cottage", "Duplex", "Penthouse", "Land", "Other"]


@dataclass
class PropertyFacts:
    name: str
    location: str
    price: str = ""
    property_type: str = ""
    bedrooms: int | None = None
    features: list[str] = field(default_factory=list)
    contact: str = ""
    description: str = ""

    def clean_dict(self) -> dict[str, Any]:
        """Only supplied facts, in the exact shape used for prompts and display."""
        data: dict[str, Any] = {"name": self.name, "location": self.location}
        if self.price:
            data["price"] = self.price
        if self.property_type:
            data["property_type"] = self.property_type
        if self.bedrooms is not None:
            data["bedrooms"] = self.bedrooms
        if self.features:
            data["key_features"] = list(self.features)
        if self.contact:
            data["contact"] = self.contact
        if self.description:
            data["description"] = self.description
        return data

    def meta_line(self) -> str:
        """e.g. "Apartment · 3 bedrooms". Never contains placeholders."""
        bits: list[str] = []
        if self.property_type:
            bits.append(self.property_type)
        if self.bedrooms is not None:
            bits.append("1 bedroom" if self.bedrooms == 1 else f"{self.bedrooms} bedrooms")
        return " · ".join(bits)

    def all_user_text(self) -> str:
        return " ".join(
            [self.name, self.location, self.price, self.property_type,
             " ".join(self.features), self.contact, self.description]
        )


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def clean_optional(value: Any) -> str:
    """Trim, collapse whitespace, strip control chars, map placeholder words to ''."""
    if value is None:
        return ""
    text = _CONTROL_RE.sub(" ", str(value))
    text = _WS_RE.sub(" ", text).strip()
    if text.lower() in _EMPTY_TOKENS:
        return ""
    return text


def clean_multiline(value: Any) -> str:
    if value is None:
        return ""
    text = _CONTROL_RE.sub(" ", str(value)).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)
    if text.lower() in _EMPTY_TOKENS:
        return ""
    return text


def _require(text: str, label: str, field_name: str, min_len: int, max_len: int) -> str:
    if not text:
        raise ValidationError(f"{label} is required.", field=field_name)
    if len(text) < min_len:
        raise ValidationError(f"{label} must be at least {min_len} characters.", field=field_name)
    if len(text) > max_len:
        raise ValidationError(f"{label} must be {max_len} characters or fewer.",
                              field=field_name,
                              hint="Shorten it and try again.")
    return text


def parse_property(form: Mapping[str, Any]) -> PropertyFacts:
    """Validate the property form. Raises ValidationError with a field name."""
    # Accept the form field names and the friendlier names used in sample_data
    # JSON, so the same file works whether it is loaded from disk or posted.
    name = _require(clean_optional(form.get("name") or form.get("property_name")),
                    "Property name", "name", 2, 80)
    location = _require(clean_optional(form.get("location")), "Location", "location", 2, 100)

    price = clean_optional(form.get("price"))
    if len(price) > 40:
        raise ValidationError("Price must be 40 characters or fewer.", field="price")
    if price and not re.search(r"\d", price):
        raise ValidationError("Price should contain a number (for example 450,000).",
                              field="price",
                              hint="Leave the field empty if you prefer not to show a price.")

    property_type = clean_optional(form.get("property_type"))
    if len(property_type) > 40:
        raise ValidationError("Property type must be 40 characters or fewer.",
                              field="property_type")

    bedrooms: int | None = None
    raw_bedrooms = clean_optional(form.get("bedrooms"))
    if raw_bedrooms:
        try:
            bedrooms = int(float(raw_bedrooms))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Bedrooms must be a whole number.",
                                  field="bedrooms") from exc
        if not 0 <= bedrooms <= 50:
            raise ValidationError("Bedrooms must be between 0 and 50.", field="bedrooms")

    raw_features = form.get("features") or form.get("amenities") or form.get("key_features")
    if isinstance(raw_features, (list, tuple)):  # JSON sample data uses a list
        raw_features = "\n".join(str(item) for item in raw_features)
    features_raw = clean_multiline(raw_features)
    features = _split_features(features_raw)
    if len(features) > 12:
        raise ValidationError("Please list at most 12 key features.", field="features",
                              hint="Put one feature per line.")
    if any(len(item) > 90 for item in features):
        raise ValidationError("Each feature must be 90 characters or fewer.", field="features")

    contact = clean_optional(form.get("contact"))
    if len(contact) > 160:
        raise ValidationError("Contact details must be 160 characters or fewer.", field="contact")

    description = clean_multiline(form.get("description"))
    if len(description) > 800:
        raise ValidationError("Additional description must be 800 characters or fewer.",
                              field="description")

    return PropertyFacts(
        name=name,
        location=location,
        price=price,
        property_type=property_type,
        bedrooms=bedrooms,
        features=features,
        contact=contact,
        description=description,
    )


def _split_features(raw: str) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[\n;,•·]+", raw)
    cleaned = []
    seen = set()
    for part in parts:
        item = _WS_RE.sub(" ", part).strip(" .-")
        if not item or item.lower() in _EMPTY_TOKENS:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
    return cleaned


def load_property_json(path: str | Path) -> PropertyFacts:
    """Load synthetic sample data from JSON, validating it through the same path."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValidationError("The property JSON must be an object.")
    return parse_property(data)


# --------------------------------------------------------------------------- #
# Upload validation
# --------------------------------------------------------------------------- #
def validate_image_selection(filenames: Iterable[str], limits: dict) -> list[str]:
    """Validate count and extensions before we touch the bytes."""
    names = [safe_original_name(name) for name in filenames if name]
    minimum, maximum = int(limits["min_images"]), int(limits["max_images"])
    if len(names) < minimum:
        raise ImageError(
            f"Please add at least {minimum} property photos.",
            hint=f"You selected {len(names)}. A good video needs {minimum}–{maximum} images.",
        )
    if len(names) > maximum:
        raise ImageError(
            f"Please use at most {maximum} property photos.",
            hint=f"You selected {len(names)}. Remove a few and try again.",
        )
    for name in names:
        if extension_of(name) not in IMAGE_EXTENSIONS:
            raise ImageError(
                f"'{name}' is not a supported image type.",
                hint="Use JPG, JPEG, PNG or WebP.",
            )
    return names


def validate_audio_filename(filename: str) -> str:
    name = safe_original_name(filename)
    if extension_of(name) not in AUDIO_EXTENSIONS:
        raise ValidationError(
            f"'{name}' is not a supported audio file.",
            field="music",
            hint="Use MP3, WAV, M4A, AAC, OGG or FLAC.",
        )
    return name


def check_file_size(size_bytes: int, limit_mb: int, label: str, field_name: str) -> None:
    if size_bytes <= 0:
        raise ValidationError(f"The {label} file is empty.", field=field_name)
    if size_bytes > limit_mb * 1024 * 1024:
        raise ValidationError(f"The {label} file is larger than {limit_mb} MB.",
                              field=field_name,
                              hint="Compress it or choose a smaller file.")
