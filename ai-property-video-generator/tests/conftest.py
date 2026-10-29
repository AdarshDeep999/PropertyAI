"""Shared fixtures.

Every test runs against temporary settings/secret files so the real
``config/secrets.json`` and music library are never touched.
"""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolate_settings(tmp_path, monkeypatch):
    """Point the settings singleton at a temp dir for every test."""
    from config.settings import store

    monkeypatch.setattr(store, "public_path", tmp_path / "settings.json")
    monkeypatch.setattr(store, "secrets_path", tmp_path / "secrets.json")
    store._secrets = None
    for env_name in ("GEMINI_API_KEY", "OPENAI_COMPAT_API_KEY", "OPENAI_TTS_API_KEY",
                     "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(env_name, raising=False)
    yield store
    store._secrets = None


@pytest.fixture
def sample_facts():
    from services.validation import PropertyFacts

    return PropertyFacts(
        name="Marina Crest Residence",
        location="12 Harbour View Lane, Portside",
        price="$685,000",
        property_type="Apartment",
        bedrooms=3,
        features=["Private balcony with sea views", "Secure parking for two cars",
                  "Fitted kitchen with stone worktops"],
        contact="Coastal & Co. Realty · +1 555 0142 · hello@coastalandco.example",
        description="A third-floor apartment close to the marina and the ferry terminal.",
    )


@pytest.fixture
def make_images(tmp_path):
    """Create N valid JPEG photos and return their paths."""
    def _make(count: int = 4, size: tuple[int, int] = (1200, 675)) -> list[Path]:
        paths = []
        colours = [(196, 148, 96), (92, 128, 150), (140, 160, 120), (170, 130, 140),
                   (120, 110, 160), (150, 140, 110), (90, 140, 160), (160, 120, 100)]
        for index in range(count):
            image = Image.new("RGB", size, colours[index % len(colours)])
            draw = ImageDraw.Draw(image)
            draw.rectangle([40, 40, size[0] - 40, size[1] - 40],
                           outline=(255, 255, 255), width=6)
            path = tmp_path / f"photo_{index + 1}.jpg"
            image.save(path, format="JPEG", quality=88)
            paths.append(path)
        return paths
    return _make


@pytest.fixture
def make_wav(tmp_path):
    """Create a valid WAV file (tone or silence)."""
    def _make(name: str = "tone.wav", seconds: float = 6.0, freq: int = 220,
              amplitude: int = 6000, silent: bool = False) -> Path:
        import array
        import math

        sample_rate = 48000
        total = int(seconds * sample_rate)
        frames = array.array("h", [0]) * total
        if not silent:
            for index in range(total):
                frames[index] = int(amplitude * math.sin(2 * math.pi * freq * index / sample_rate))
        path = tmp_path / name
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(frames.tobytes())
        return path
    return _make


@pytest.fixture
def client():
    """Flask test client with a valid CSRF token already in the session."""
    import app as app_module

    app_module.app.config.update(TESTING=True)
    test_client = app_module.app.test_client()
    with test_client.session_transaction() as session:
        session["_csrf"] = "test-csrf-token"
    test_client.csrf = "test-csrf-token"  # type: ignore[attr-defined]
    return test_client


@pytest.fixture
def csrf_headers():
    return {"X-CSRF-Token": "test-csrf-token"}


@pytest.fixture
def sample_json_path():
    return ROOT / "sample_data" / "property_sample.json"


@pytest.fixture
def sample_image_paths():
    return sorted((ROOT / "sample_data" / "images").glob("*.jpg"))


@pytest.fixture
def script_payload(sample_facts):
    from services.script_generator import word_budget
    from services.template_script import generate_template_script

    budget = word_budget(4)
    return generate_template_script(
        sample_facts, 4,
        max_words_per_scene=budget["scene"],
        max_words_intro=budget["intro"],
        max_words_cta=budget["cta"],
    )


@pytest.fixture
def json_text():
    def _cleanup(payload) -> str:
        return json.dumps(payload)
    return _cleanup
