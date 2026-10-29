"""Voice-over fallback policy (engines are faked; no network or audio here)."""

from __future__ import annotations

import pytest

from services import tts
from services.errors import TTSError


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    """Replace synthesis/validation so fallback logic is tested in isolation."""
    monkeypatch.setattr(tts.time, "sleep", lambda _seconds: None)

    def install(behaviour):
        def fake_synthesise(engine, texts, voice, speed, out_dir, store):
            out_dir.mkdir(parents=True, exist_ok=True)
            result = behaviour(engine, len(texts))
            if isinstance(result, Exception):
                raise result
            clips = []
            for index in range(result):
                path = out_dir / f"{engine}_{index}.wav"
                path.write_bytes(b"fake-audio" * 100)
                clips.append(path)
            return clips

        monkeypatch.setattr(tts, "_synthesise", fake_synthesise)
        monkeypatch.setattr(tts, "_validate_clips", lambda clips: [1.4] * len(clips))

    return install


def test_primary_engine_success(fake_engine, isolate_settings, tmp_path):
    fake_engine(lambda engine, count: count)
    result = tts.synthesize(["one", "two"], tmp_path / "out", settings_store=isolate_settings)
    assert result.engine == "edge"
    assert result.fallback_used is False
    assert len(result.clips) == 2
    assert result.total_seconds == pytest.approx(2.8)


def test_fallback_is_used_and_reported(fake_engine, isolate_settings, tmp_path):
    def behaviour(engine, count):
        if engine == "edge":
            return TTSError("Edge is unavailable", retryable=True)
        return count

    fake_engine(behaviour)
    result = tts.synthesize(["one", "two"], tmp_path / "out", settings_store=isolate_settings)
    assert result.engine == "windows_sapi"
    assert result.fallback_used is True
    assert result.primary_engine == "edge"
    assert "Edge" in (result.primary_error or "")
    assert result.warnings and "fallback engine" in result.warnings[0]
    assert result.to_public()["fallback_used"] is True


def test_no_fallback_raises_with_continue_option(fake_engine, isolate_settings, tmp_path):
    isolate_settings.update_public({"tts": {"fallback_engine": "none"}})
    fake_engine(lambda engine, count: TTSError("Edge is down", retryable=False))

    with pytest.raises(TTSError) as error:
        tts.synthesize(["one"], tmp_path / "out", settings_store=isolate_settings)
    assert error.value.details["can_continue_without_narration"] is True
    assert error.value.details["primary_engine"] == "edge"


def test_both_engines_failing_is_reported_clearly(fake_engine, isolate_settings, tmp_path):
    fake_engine(lambda engine, count: TTSError(f"{engine} failed", retryable=False))

    with pytest.raises(TTSError) as error:
        tts.synthesize(["one"], tmp_path / "out", settings_store=isolate_settings)
    details = error.value.details
    assert details["primary_engine"] == "edge"
    assert details["fallback_engine"] == "windows_sapi"
    assert "primary_error" in details and "fallback_error" in details


def test_transient_failure_is_retried_once(fake_engine, isolate_settings, tmp_path):
    calls = {"count": 0}

    def behaviour(engine, count):
        calls["count"] += 1
        if calls["count"] == 1:
            return TTSError("temporary network issue", retryable=True)
        return count

    fake_engine(behaviour)
    result = tts.synthesize(["one"], tmp_path / "out", settings_store=isolate_settings)
    assert calls["count"] == 2
    assert result.engine == "edge"
    assert result.fallback_used is False


def test_non_retryable_failure_is_not_retried(fake_engine, isolate_settings, tmp_path):
    isolate_settings.update_public({"tts": {"fallback_engine": "none"}})
    calls = {"count": 0}

    def behaviour(engine, count):
        calls["count"] += 1
        return TTSError("bad key", retryable=False)

    fake_engine(behaviour)
    with pytest.raises(TTSError):
        tts.synthesize(["one"], tmp_path / "out", settings_store=isolate_settings)
    assert calls["count"] == 1


def test_empty_text_is_rejected_before_any_call(fake_engine, isolate_settings, tmp_path):
    fake_engine(lambda engine, count: pytest.fail("must not synthesise empty text"))
    with pytest.raises(TTSError):
        tts.synthesize(["   "], tmp_path / "out", settings_store=isolate_settings)


def test_narration_message_distinguishes_fallback():
    from app import _narration_message

    class FakeResult:
        engine = "windows_sapi"
        primary_engine = "edge"
        fallback_used = True

    message = _narration_message(FakeResult())
    assert "Windows" in message
    assert "because" in message
