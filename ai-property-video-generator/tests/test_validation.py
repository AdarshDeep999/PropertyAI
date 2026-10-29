"""Property and upload validation."""

from __future__ import annotations

import pytest

from services.errors import ImageError, ValidationError
from services.validation import (
    load_property_json,
    parse_property,
    validate_audio_filename,
    validate_image_selection,
)

LIMITS = {"min_images": 4, "max_images": 8, "max_image_mb": 15}


def test_name_is_required():
    with pytest.raises(ValidationError) as error:
        parse_property({"location": "Portside"})
    assert error.value.field == "name"
    assert "required" in error.value.message.lower()


def test_location_is_required():
    with pytest.raises(ValidationError) as error:
        parse_property({"name": "Marina Crest"})
    assert error.value.field == "location"


def test_optional_fields_default_to_empty_not_none():
    facts = parse_property({"name": "Marina Crest", "location": "Portside"})
    assert facts.price == ""
    assert facts.bedrooms is None
    assert facts.features == []
    assert facts.meta_line() == ""
    assert "None" not in str(facts.clean_dict())
    assert "null" not in str(facts.clean_dict())


def test_placeholder_words_are_treated_as_empty():
    facts = parse_property({
        "name": "Marina Crest", "location": "Portside",
        "price": "None", "bedrooms": "null", "contact": " undefined ",
        "features": "none", "description": "N/A",
    })
    assert facts.price == ""
    assert facts.bedrooms is None
    assert facts.contact == ""
    assert facts.features == []
    assert facts.description == ""


def test_price_must_contain_a_number():
    with pytest.raises(ValidationError) as error:
        parse_property({"name": "Marina Crest", "location": "Portside", "price": "On request"})
    assert error.value.field == "price"


def test_bedrooms_are_bounded():
    with pytest.raises(ValidationError):
        parse_property({"name": "Marina Crest", "location": "Portside", "bedrooms": "51"})
    with pytest.raises(ValidationError):
        parse_property({"name": "Marina Crest", "location": "Portside", "bedrooms": "-1"})
    with pytest.raises(ValidationError):
        parse_property({"name": "Marina Crest", "location": "Portside", "bedrooms": "three"})


def test_features_are_split_and_deduplicated():
    facts = parse_property({
        "name": "Marina Crest", "location": "Portside",
        "features": "Balcony\nSecure parking; balcony\n\nFitted kitchen",
    })
    assert facts.features == ["Balcony", "Secure parking", "Fitted kitchen"]


def test_too_many_features_rejected():
    with pytest.raises(ValidationError) as error:
        parse_property({"name": "Marina Crest", "location": "Portside",
                        "features": "\n".join(f"Feature {index}" for index in range(13))})
    assert error.value.field == "features"


def test_meta_line_never_contains_placeholders():
    facts = parse_property({"name": "Marina Crest", "location": "Portside",
                            "property_type": "Apartment", "bedrooms": "1"})
    assert facts.meta_line() == "Apartment · 1 bedroom"


def test_image_count_minimum():
    with pytest.raises(ImageError):
        validate_image_selection(["a.jpg", "b.jpg"], LIMITS)


def test_image_count_maximum():
    with pytest.raises(ImageError):
        validate_image_selection([f"{index}.jpg" for index in range(9)], LIMITS)


def test_image_extensions_are_checked():
    with pytest.raises(ImageError):
        validate_image_selection(["a.jpg", "b.png", "c.gif", "d.webp"], LIMITS)
    names = validate_image_selection(["a.jpg", "b.JPEG", "c.png", "d.webp"], LIMITS)
    assert names == ["a.jpg", "b.JPEG", "c.png", "d.webp"]


def test_audio_extension_is_checked():
    with pytest.raises(ValidationError):
        validate_audio_filename("track.exe")
    assert validate_audio_filename("song.MP3") == "song.MP3"


def test_sample_property_json_loads(sample_json_path):
    facts = load_property_json(sample_json_path)
    assert facts.name == "Marina Crest Residence"
    assert facts.bedrooms == 3
    assert len(facts.features) == 5
    assert facts.price.startswith("$")
