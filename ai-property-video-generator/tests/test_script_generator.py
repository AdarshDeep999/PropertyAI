"""Script generation: success, malformed output, error mapping and fallback.

These tests use an injected fake client, so they prove the *app's* retry and
fallback logic. They do not prove any provider works; the live test file covers
that separately.
"""

from __future__ import annotations

import json

import pytest

from services.errors import ProviderAuthError, ProviderNetworkError, ProviderRateLimitError
from services.script_generator import generate_script, word_budget
from services.template_script import generate_template_script


def _payload(facts, count=4):
    budget = word_budget(count)
    return generate_template_script(
        facts, count,
        max_words_per_scene=budget["scene"],
        max_words_intro=budget["intro"],
        max_words_cta=budget["cta"],
    )


def test_no_key_falls_back_without_calling_the_provider(sample_facts, isolate_settings):
    def client(*args, **kwargs):
        raise AssertionError("client must not be called without a key")

    result = generate_script(sample_facts, 4, settings_store=isolate_settings, client=client)
    assert result.source == "template"
    assert "No Google Gemini API key" in result.fallback_reason
    assert result.attempts == 0


def test_force_template_skips_the_provider(sample_facts, isolate_settings):
    isolate_settings.set_secret("gemini", "test-key-abcdef")
    isolate_settings.update_public({"llm": {"force_template": True}})

    def client(*args, **kwargs):
        raise AssertionError("client must not be called in template mode")

    result = generate_script(sample_facts, 4, settings_store=isolate_settings, client=client)
    assert result.source == "template"
    assert "Template mode" in result.fallback_reason


def test_valid_ai_response_is_used(sample_facts, isolate_settings):
    isolate_settings.set_secret("gemini", "test-key-abcdef")
    payload = _payload(sample_facts, 4)

    result = generate_script(
        sample_facts, 4, settings_store=isolate_settings,
        client=lambda *args: json.dumps(payload), sleep=lambda _seconds: None,
    )
    assert result.source == "ai"
    assert result.attempts == 1
    assert len(result.data["scenes"]) == 4
    assert result.data["headline"] == payload["headline"]
    assert result.to_public()["is_ai"] is True


def test_code_fenced_json_is_accepted(sample_facts, isolate_settings):
    isolate_settings.set_secret("gemini", "test-key-abcdef")
    payload = _payload(sample_facts, 4)
    fenced = "Here you go:\n```json\n" + json.dumps(payload) + "\n```"

    result = generate_script(sample_facts, 4, settings_store=isolate_settings,
                             client=lambda *args: fenced, sleep=lambda _s: None)
    assert result.source == "ai"


def test_malformed_json_retries_then_falls_back(sample_facts, isolate_settings):
    isolate_settings.set_secret("gemini", "test-key-abcdef")
    calls = {"count": 0}

    def client(*args):
        calls["count"] += 1
        return "I'm sorry, I can't help with that."

    result = generate_script(sample_facts, 4, settings_store=isolate_settings,
                             client=client, sleep=lambda _s: None)
    assert result.source == "template"
    assert result.attempts == 2
    assert calls["count"] == 2
    assert "could not be validated" in result.fallback_reason


def test_invalid_key_is_not_retried(sample_facts, isolate_settings):
    isolate_settings.set_secret("gemini", "test-key-abcdef")
    calls = {"count": 0}

    def client(*args):
        calls["count"] += 1
        raise ProviderAuthError("Gemini")

    result = generate_script(sample_facts, 4, settings_store=isolate_settings,
                             client=client, sleep=lambda _s: None)
    assert result.source == "template"
    assert result.attempts == 1
    assert calls["count"] == 1
    assert "rejected the API key" in result.fallback_reason


def test_rate_limit_is_retried_then_falls_back(sample_facts, isolate_settings):
    isolate_settings.set_secret("gemini", "test-key-abcdef")
    calls = {"count": 0}

    def client(*args):
        calls["count"] += 1
        raise ProviderRateLimitError("Gemini")

    result = generate_script(sample_facts, 4, settings_store=isolate_settings,
                             client=client, sleep=lambda _s: None)
    assert result.attempts == 2
    assert calls["count"] == 2
    assert "rate limiting" in result.fallback_reason


def test_network_error_falls_back(sample_facts, isolate_settings):
    isolate_settings.set_secret("gemini", "test-key-abcdef")

    def client(*args):
        raise ProviderNetworkError("Gemini")

    result = generate_script(sample_facts, 4, settings_store=isolate_settings,
                             client=client, sleep=lambda _s: None)
    assert result.source == "template"
    assert "Could not reach" in result.fallback_reason


def test_invented_fact_is_rejected(sample_facts, isolate_settings):
    isolate_settings.set_secret("gemini", "test-key-abcdef")
    payload = _payload(sample_facts, 4)
    payload["intro_narration"] = "Yours for only $1,999,000."

    result = generate_script(sample_facts, 4, settings_store=isolate_settings,
                             client=lambda *args: json.dumps(payload), sleep=lambda _s: None)
    assert result.source == "template"
    assert "could not be validated" in result.fallback_reason


def test_wrong_scene_count_is_rejected(sample_facts, isolate_settings):
    isolate_settings.set_secret("gemini", "test-key-abcdef")
    payload = _payload(sample_facts, 3)  # only 3 scenes for 4 photos

    result = generate_script(sample_facts, 4, settings_store=isolate_settings,
                             client=lambda *args: json.dumps(payload), sleep=lambda _s: None)
    assert result.source == "template"


def test_overlong_narration_is_rejected(sample_facts, isolate_settings):
    isolate_settings.set_secret("gemini", "test-key-abcdef")
    payload = _payload(sample_facts, 4)
    payload["intro_narration"] = "word " * 40

    result = generate_script(sample_facts, 4, settings_store=isolate_settings,
                             client=lambda *args: json.dumps(payload), sleep=lambda _s: None)
    assert result.source == "template"


def test_openai_compatible_requires_base_url_key_or_localhost(sample_facts, isolate_settings):
    isolate_settings.update_public({"llm": {"provider": "openai_compatible",
                                            "model": "gpt-4o-mini", "base_url": ""}})

    def client(*args):
        raise AssertionError("no key and no local endpoint: must not call")

    result = generate_script(sample_facts, 4, settings_store=isolate_settings, client=client)
    assert result.source == "template"
    assert "No OpenAI-compatible" in result.fallback_reason


@pytest.mark.parametrize("count", [4, 5, 6, 7, 8])
def test_word_budget_shrinks_with_more_photos(count):
    budget = word_budget(count)
    assert budget["scene"] >= 7
    assert budget["total"] == budget["intro"] + budget["scene"] * count + budget["cta"]
