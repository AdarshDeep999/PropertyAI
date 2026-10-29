"""Path traversal protection and filename safety."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.errors import SecurityError
from services.storage import (
    human_size,
    is_id,
    new_id,
    safe_join,
    safe_original_name,
    slugify,
)


def test_safe_join_allows_nested_paths(tmp_path):
    result = safe_join(tmp_path, "jobs", "abc", "file.mp4")
    assert result == (tmp_path / "jobs" / "abc" / "file.mp4").resolve()


def test_safe_join_blocks_traversal(tmp_path):
    with pytest.raises(SecurityError):
        safe_join(tmp_path, "..", "etc", "passwd")
    with pytest.raises(SecurityError):
        safe_join(tmp_path, "jobs", "..", "..", "outside.txt")


def test_safe_join_blocks_absolute_paths(tmp_path):
    with pytest.raises(SecurityError):
        safe_join(tmp_path, "/etc/passwd")
    with pytest.raises(SecurityError):
        safe_join(tmp_path, "C:\\Windows\\System32")


def test_safe_original_name_strips_directories():
    assert safe_original_name("../../secret.jpg") == "secret.jpg"
    assert safe_original_name("C:\\Users\\me\\photo.png") == "photo.png"
    assert safe_original_name("normal.webp") == "normal.webp"


def test_safe_original_name_handles_empty_and_control_chars():
    assert safe_original_name("") == "upload"
    assert safe_original_name("bad\x00name.jpg") == "badname.jpg"
    assert safe_original_name("line\nbreak.jpg") == "linebreak.jpg"
    assert len(safe_original_name("x" * 500)) <= 120


def test_ids_are_opaque_hex():
    value = new_id()
    assert is_id(value)
    assert len(value) == 32
    assert not is_id("../etc")
    assert not is_id("")
    assert not is_id("abc")


def test_slugify():
    assert slugify("Marina Crest Residence!") == "marina-crest-residence"
    assert slugify("   ") == "property"
    assert len(slugify("a" * 200)) <= 60


def test_human_size():
    assert human_size(500) == "500 B"
    assert human_size(2048) == "2.0 KB"
    assert human_size(5 * 1024 * 1024) == "5.0 MB"
