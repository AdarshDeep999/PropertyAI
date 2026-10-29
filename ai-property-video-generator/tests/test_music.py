"""Music library: validation, metadata, deletion and one-off uploads."""

from __future__ import annotations

import pytest

from services import music
from services.errors import MusicError, NotFoundError

LIMITS = {"max_audio_mb": 25, "min_audio_seconds": 1.0}


@pytest.fixture(autouse=True)
def isolated_library(tmp_path, monkeypatch):
    monkeypatch.setattr(music, "MUSIC_DIR", tmp_path)
    monkeypatch.setattr(music, "INDEX_PATH", tmp_path / "index.json")
    return tmp_path


def test_add_list_and_delete_track(make_wav):
    wav = make_wav("piano.wav", seconds=4.0)
    track = music.add_to_library("piano.wav", wav.read_bytes(), LIMITS)
    assert track["name"] == "piano.wav"
    assert 3.9 < track["duration"] < 4.1
    assert track["audio_url"].endswith(track["id"])

    tracks = music.list_tracks()
    assert len(tracks) == 1
    assert music.track_path(track["id"]).is_file()

    music.delete_track(track["id"])
    assert music.list_tracks() == []
    with pytest.raises(NotFoundError):
        music.track_path(track["id"])


def test_metadata_is_read_when_present(make_wav):
    from mutagen.wave import WAVE
    from mutagen.id3 import TIT2, TPE1

    wav = make_wav("tagged.wav", seconds=2.0)
    audio = WAVE(str(wav))
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text="Evening Calm"))
    audio.tags.add(TPE1(encoding=3, text="Test Artist"))
    audio.save()

    track = music.add_to_library("tagged.wav", wav.read_bytes(), LIMITS)
    assert track["title"] == "Evening Calm"
    assert track["artist"] == "Test Artist"
    assert "Test Artist" in track["metadata_label"]


def test_corrupt_audio_is_rejected():
    with pytest.raises(MusicError):
        music.add_to_library("broken.mp3", b"not really audio" * 100, LIMITS)


def test_unsupported_extension_is_rejected(make_wav):
    with pytest.raises(MusicError):
        music.add_to_library("song.exe", make_wav().read_bytes(), LIMITS)


def test_too_short_audio_is_rejected(make_wav):
    with pytest.raises(MusicError):
        music.add_to_library("blip.wav", make_wav(seconds=0.2).read_bytes(), LIMITS)


def test_oversized_audio_is_rejected(make_wav):
    with pytest.raises(MusicError):
        music.add_to_library("big.wav", make_wav(seconds=3.0).read_bytes(),
                             {"max_audio_mb": 0, "min_audio_seconds": 1.0})


def test_one_off_upload_is_not_added_to_the_library(make_wav, tmp_path):
    wav = make_wav("oneoff.wav", seconds=3.0)
    path = music.save_temp_track("oneoff.wav", wav.read_bytes(), tmp_path / "job", LIMITS)
    assert path.is_file()
    assert music.list_tracks() == []


def test_corrupt_one_off_upload_is_rejected(tmp_path):
    with pytest.raises(MusicError):
        music.save_temp_track("bad.mp3", b"nope" * 200, tmp_path / "job", LIMITS)


def test_library_usage(make_wav):
    music.add_to_library("a.wav", make_wav("a.wav", seconds=2.0).read_bytes(), LIMITS)
    usage = music.library_usage()
    assert usage["count"] == 1
    assert usage["bytes"] > 0


def test_missing_track_file_is_reported(isolated_library):
    with pytest.raises(NotFoundError):
        music.track_path("0" * 32)
