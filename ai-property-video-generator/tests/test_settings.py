"""Settings storage, masking and secret handling."""

from __future__ import annotations

import json
import sys

import pytest

from config.settings import SettingsStore, _dpapi_protect, _dpapi_unprotect, dpapi_available
from services.errors import SettingsError

FAKE_KEY = "AIzaSyTESTKEY1234567890abcdef"


def test_defaults_are_complete(isolate_settings):
    public = isolate_settings.public()
    assert public["llm"]["provider"] == "gemini"
    assert public["tts"]["engine"] == "edge"
    assert 0.0 <= public["video"]["music_volume"] <= 1.0


def test_unknown_section_is_rejected(isolate_settings):
    with pytest.raises(SettingsError):
        isolate_settings.update_public({"nope": {"x": 1}})


def test_unknown_provider_is_rejected(isolate_settings):
    with pytest.raises(SettingsError):
        isolate_settings.update_public({"llm": {"provider": "skynet"}})


def test_speed_bounds(isolate_settings):
    with pytest.raises(SettingsError):
        isolate_settings.update_public({"tts": {"speed": 3.0}})
    updated = isolate_settings.update_public({"tts": {"speed": 1.15}})
    assert updated["tts"]["speed"] == 1.15


def test_music_volume_bounds(isolate_settings):
    with pytest.raises(SettingsError):
        isolate_settings.update_public({"video": {"music_volume": 2}})


def test_base_url_must_be_http(isolate_settings):
    with pytest.raises(SettingsError):
        isolate_settings.update_public({"llm": {"base_url": "ftp://example.com"}})


def test_secret_is_stored_and_masked(isolate_settings):
    status = isolate_settings.set_secret("gemini", FAKE_KEY)
    assert status["configured"] is True
    assert status["removable"] is True
    assert status["masked"].endswith(FAKE_KEY[-4:])
    assert FAKE_KEY not in status["masked"]
    assert isolate_settings.get_secret("gemini") == FAKE_KEY


def test_full_key_is_never_in_public_status(isolate_settings):
    isolate_settings.set_secret("gemini", FAKE_KEY)
    payload = json.dumps(isolate_settings.all_secret_status(), ensure_ascii=False)
    assert FAKE_KEY not in payload
    assert "••••" in payload
    # Also safe when the API serialises with ASCII escapes.
    assert FAKE_KEY not in json.dumps(isolate_settings.all_secret_status())


def test_short_key_is_rejected(isolate_settings):
    with pytest.raises(SettingsError):
        isolate_settings.set_secret("gemini", "short")


def test_empty_key_is_rejected(isolate_settings):
    with pytest.raises(SettingsError):
        isolate_settings.set_secret("gemini", "   ")


def test_unknown_credential_is_rejected(isolate_settings):
    with pytest.raises(SettingsError):
        isolate_settings.set_secret("not_a_service", FAKE_KEY)


def test_secret_can_be_deleted(isolate_settings):
    isolate_settings.set_secret("gemini", FAKE_KEY)
    status = isolate_settings.delete_secret("gemini")
    assert status["configured"] is False
    assert isolate_settings.get_secret("gemini") is None


def test_secret_file_never_contains_the_raw_key(isolate_settings):
    isolate_settings.set_secret("gemini", FAKE_KEY)
    raw = isolate_settings.secrets_path.read_bytes()
    assert FAKE_KEY.encode() not in raw
    # The document records which protection was used.
    document = json.loads(raw)
    assert document["protection"] in ("dpapi", "plain")
    assert document["payload"]


def test_corrupt_secret_file_does_not_crash(isolate_settings):
    isolate_settings.secrets_path.parent.mkdir(parents=True, exist_ok=True)
    isolate_settings.secrets_path.write_text("{not json", encoding="utf-8")
    isolate_settings._secrets = None
    assert isolate_settings.get_secret("gemini") is None
    assert isolate_settings.set_secret("gemini", FAKE_KEY)["configured"] is True


def test_environment_key_is_read_and_not_removable(isolate_settings, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    status = isolate_settings.secret_status("gemini")
    assert status["configured"] is True
    assert status["source"] == "environment"
    assert status["removable"] is False
    assert isolate_settings.get_secret("gemini") == FAKE_KEY
    after_delete = isolate_settings.delete_secret("gemini")
    assert after_delete["configured"] is True  # still supplied by the environment


def test_settings_file_is_separate_from_secrets(isolate_settings, tmp_path):
    store = SettingsStore(public_path=tmp_path / "public.json",
                          secrets_path=tmp_path / "hidden.json")
    store.update_public({"video": {"music_volume": 0.5}})
    store.set_secret("gemini", FAKE_KEY)
    assert store.public_path.is_file()
    assert store.secrets_path.is_file()
    assert FAKE_KEY not in store.public_path.read_text(encoding="utf-8")


@pytest.mark.skipif(not dpapi_available(), reason="DPAPI is Windows-only")
def test_dpapi_round_trip():
    encrypted = _dpapi_protect(b"secret payload")
    assert b"secret payload" not in encrypted
    assert _dpapi_unprotect(encrypted) == b"secret payload"


def test_public_catalog_exposes_credential_slot_names():
    """The Settings UI saves a key by looking up `secret` in /api/settings.

    Regression: popping `secret` from the public catalogue made the browser post
    an empty name, so saving a Gemini key failed with "Unknown credential".
    """
    from config.providers import public_catalog

    catalog = public_catalog()
    for provider_id, meta in catalog["llm"].items():
        assert meta.get("secret") in {"gemini", "openai_compat"}, provider_id
    assert catalog["tts"]["openai"]["secret"] == "openai_tts"
    assert catalog["tts"]["elevenlabs"]["secret"] == "elevenlabs"
    assert not catalog["tts"]["edge"].get("secret")
    assert not catalog["tts"]["windows_sapi"].get("secret")
    # Only the slot name is exposed, never a credential value.
    assert "api_key" not in repr(catalog).lower()
