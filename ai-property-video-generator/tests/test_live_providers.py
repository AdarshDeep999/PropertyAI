"""Opt-in tests that make REAL network calls.

Run with:
    RUN_LIVE=1 python -m pytest tests/test_live_providers.py -v

Only invalid credentials and the free Edge voice service are exercised, so these
tests never cost money and never need a paid key. A valid Gemini key is tested
manually through the Settings page and recorded in DEMO.md.
"""

from __future__ import annotations

import os

import pytest

# Import the modules, not the functions: a module-level name beginning with
# "test_" would be collected by pytest as a test (and fail for returning data).
from services import script_generator, tts

pytestmark = pytest.mark.live

requires_live = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1",
    reason="live network test: set RUN_LIVE=1 to enable",
)


@requires_live
def test_gemini_rejects_an_invalid_key(isolate_settings):
    result = script_generator.test_llm_connection(
        isolate_settings, provider_id="gemini", model="gemini-3.5-flash",
        base_url="", api_key="invalid-key-for-testing-000000",
    )
    assert result["ok"] is False
    assert result["code"] in ("invalid_credentials", "provider_permission")
    assert "key" in result["message"].lower()


@requires_live
def test_openai_rejects_an_invalid_key(isolate_settings):
    result = script_generator.test_llm_connection(
        isolate_settings, provider_id="openai_compatible", model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        api_key="sk-invalid-key-for-testing-000000",
    )
    assert result["ok"] is False
    assert result["code"] == "invalid_credentials"


@requires_live
def test_edge_voice_engine_produces_real_audio(isolate_settings):
    result = tts.test_engine("edge", "en-US-AriaNeural", settings_store=isolate_settings)
    assert result["ok"] is True, result
    assert "audio clip" in result["message"]
