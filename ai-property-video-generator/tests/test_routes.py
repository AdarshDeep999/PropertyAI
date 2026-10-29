"""HTTP routes, validation responses, CSRF, and the full job flow.

The renderer and TTS engine are replaced with fakes here so routing and job
plumbing are tested quickly; tests/test_video_render.py covers the real pipeline.
"""

from __future__ import annotations

import io
import time

import pytest

import app as app_module
from config.settings import store
from services import music
from services.tts import TTSResult

FAKE_KEY = "AIzaSyROUTETEST1234567890"


@pytest.fixture
def fake_render(monkeypatch):
    def _fake(request):
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"\x00" * 70_000)
        poster = request.output_path.with_suffix(".jpg")
        poster.write_bytes(b"\xff\xd8\xff" + b"\x00" * 4_000)
        return {
            "duration": 22.4, "size_bytes": 70_000, "width": 1920, "height": 1080,
            "fps": 30, "video_codec": "h264", "audio_codec": "aac" if request.narration_clips else None,
            "has_audio": bool(request.narration_clips), "verified": True,
            "path": str(request.output_path), "poster_path": str(poster),
            "music_used": bool(request.music_path), "music_name": request.music_name,
            "narration_used": bool(request.narration_clips),
            "timeline": {"total": 22.4, "transition": 0.6, "scenes": []},
            "warnings": [], "resolution": "1920×1080", "tone": "warm",
        }
    monkeypatch.setattr(app_module, "render_video", _fake)
    return _fake


@pytest.fixture
def fake_tts(monkeypatch):
    def _fake(texts, out_dir, settings_store=None, progress=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        clips = []
        for index in range(len(texts)):
            path = out_dir / f"clip_{index}.wav"
            path.write_bytes(b"audio" * 400)
            clips.append(path)
        return TTSResult(engine="edge", voice="en-US-AriaNeural", clips=clips,
                         durations=[1.2] * len(clips))
    monkeypatch.setattr(app_module, "synthesize", _fake)
    return _fake


def _image_form(images, **overrides):
    data = {"name": "Marina Crest Residence", "location": "Portside",
            "music_mode": "none", "enable_narration": "1"}
    data.update(overrides)
    data["images"] = [(io.BytesIO(path.read_bytes()), path.name) for path in images]
    return data


def _wait_for_job(client, job_id, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/jobs/{job_id}").get_json()
        if payload["status"] in ("done", "failed", "cancelled"):
            return payload
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in time")


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
def test_index_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Property details" in body
    assert "Generate video" in body
    assert "Background music" in body
    assert "csrf" in body.lower()


def test_settings_page_renders(client):
    response = client.get("/settings")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "AI providers and voices" in body
    assert "What needs a key" in body
    assert "How keys are stored" not in body


def test_health_reports_ffmpeg(client):
    payload = client.get("/api/health").get_json()
    assert payload["ok"] is True
    names = {check["name"]: check for check in payload["checks"]}
    assert names["FFmpeg"]["ok"] is True


def test_unknown_page_renders_friendly_error(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert "That page does not exist" in response.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# Settings and secrets
# --------------------------------------------------------------------------- #
def test_settings_endpoint_masks_secrets(client):
    store.set_secret("gemini", FAKE_KEY)
    response = client.get("/api/settings")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert FAKE_KEY not in text
    payload = response.get_json()
    assert payload["secrets"]["gemini"]["configured"] is True
    assert payload["secrets"]["gemini"]["masked"].endswith(FAKE_KEY[-4:])


def test_index_page_never_contains_the_full_key(client):
    store.set_secret("gemini", FAKE_KEY)
    body = client.get("/").get_data(as_text=True)
    assert FAKE_KEY not in body


def test_csrf_is_required_for_mutations(client):
    response = client.post("/api/settings", json={"video": {"music_volume": 0.4}})
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "forbidden"


def test_settings_can_be_saved(client, csrf_headers):
    response = client.post(
        "/api/settings",
        json={"llm": {"provider": "openai_compatible", "model": "gpt-4o-mini",
                      "base_url": "http://localhost:11434/v1"}},
        headers=csrf_headers,
    )
    assert response.status_code == 200
    assert response.get_json()["settings"]["llm"]["provider"] == "openai_compatible"


def test_invalid_settings_are_rejected(client, csrf_headers):
    response = client.post("/api/settings", json={"tts": {"speed": 9}},
                           headers=csrf_headers)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "settings_error"


def test_secret_save_and_delete(client, csrf_headers):
    saved = client.post("/api/settings/secret",
                        json={"name": "gemini", "value": FAKE_KEY},
                        headers=csrf_headers)
    assert saved.status_code == 200
    assert saved.get_json()["secret"]["configured"] is True
    assert FAKE_KEY not in saved.get_data(as_text=True)

    removed = client.delete("/api/settings/secret/gemini", headers=csrf_headers)
    assert removed.status_code == 200
    assert removed.get_json()["secret"]["configured"] is False


def test_local_only_guard(client):
    response = client.get("/api/settings", environ_base={"REMOTE_ADDR": "10.1.2.3"})
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# Script endpoint
# --------------------------------------------------------------------------- #
def test_script_endpoint_uses_template(client, csrf_headers):
    response = client.post("/api/script", json={
        "property": {"name": "Marina Crest", "location": "Portside"},
        "image_count": 4, "force_template": True,
    }, headers=csrf_headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["script"]["source"] == "template"
    assert len(payload["script"]["data"]["scenes"]) == 4
    assert payload["estimate"]["total"] > 0


def test_script_endpoint_validates_property(client, csrf_headers):
    response = client.post("/api/script", json={
        "property": {"name": "Marina Crest"}, "image_count": 4,
    }, headers=csrf_headers)
    assert response.status_code == 400
    assert response.get_json()["error"]["field"] == "location"


def test_script_endpoint_validates_image_count(client, csrf_headers):
    response = client.post("/api/script", json={
        "property": {"name": "Marina Crest", "location": "Portside"}, "image_count": 2,
    }, headers=csrf_headers)
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
def test_generate_rejects_too_few_images(client, csrf_headers, make_images):
    images = make_images(2)
    response = client.post("/api/generate", data=_image_form(images),
                           headers=csrf_headers, content_type="multipart/form-data")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_image"


def test_generate_rejects_unsupported_image_type(client, csrf_headers, make_images):
    images = make_images(4)
    data = _image_form(images)
    data["images"] = [(io.BytesIO(path.read_bytes()), f"photo{index}.gif")
                      for index, path in enumerate(images)]
    response = client.post("/api/generate", data=data, headers=csrf_headers,
                           content_type="multipart/form-data")
    assert response.status_code == 400
    assert "supported image type" in response.get_json()["error"]["message"]


def test_generate_rejects_missing_property_name(client, csrf_headers, make_images):
    images = make_images(4)
    response = client.post("/api/generate", data=_image_form(images, name=""),
                           headers=csrf_headers, content_type="multipart/form-data")
    assert response.status_code == 400
    assert response.get_json()["error"]["field"] == "name"


def test_generate_with_narration_end_to_end(client, csrf_headers, make_images,
                                           fake_render, fake_tts):
    images = make_images(4)
    response = client.post("/api/generate",
                           data=_image_form(images, force_template="1"),
                           headers=csrf_headers, content_type="multipart/form-data")
    assert response.status_code == 202
    job_id = response.get_json()["job"]["id"]

    job = _wait_for_job(client, job_id)
    assert job["status"] == "done", job.get("error")
    assert job["progress"] == 100

    stages = {stage["id"]: stage["status"] for stage in job["stages"]}
    assert stages["render"] == "done"
    assert stages["verify"] == "done"
    assert stages["ready"] == "done"

    result = job["result"]
    assert result["duration"] == 22.4
    assert result["script"]["source"] == "template"
    assert result["narration"]["engine"] == "edge"
    assert any("Template mode" in warning for warning in job["warnings"])

    video = client.get(f"/video/{job_id}")
    assert video.status_code == 200
    assert video.mimetype == "video/mp4"

    download = client.get(f"/download/{job_id}")
    assert download.status_code == 200
    assert "attachment" in download.headers["Content-Disposition"]

    poster = client.get(f"/poster/{job_id}")
    assert poster.status_code == 200
    assert poster.mimetype == "image/jpeg"


def test_narration_disabled_makes_no_tts_calls(client, csrf_headers, make_images,
                                               fake_render, monkeypatch):
    def _must_not_run(*args, **kwargs):
        raise AssertionError("TTS must not be called when narration is off")

    monkeypatch.setattr(app_module, "synthesize", _must_not_run)

    data = _image_form(make_images(4), force_template="1")
    del data["enable_narration"]
    response = client.post("/api/generate", data=data, headers=csrf_headers,
                           content_type="multipart/form-data")
    assert response.status_code == 202
    job = _wait_for_job(client, response.get_json()["job"]["id"])
    assert job["status"] == "done", job.get("error")
    assert job["result"]["narration"] is None
    stages = {stage["id"]: stage["status"] for stage in job["stages"]}
    assert stages["narration"] == "skipped"


def test_music_library_mode_requires_a_track(client, csrf_headers, make_images):
    data = _image_form(make_images(4), music_mode="library")
    response = client.post("/api/generate", data=data, headers=csrf_headers,
                           content_type="multipart/form-data")
    assert response.status_code == 400
    assert response.get_json()["error"]["field"] == "music"


def test_job_cancel_before_start_is_reported(client, csrf_headers, make_images,
                                             fake_render, fake_tts):
    # Start a job, then cancel as soon as it exists.
    response = client.post("/api/generate", data=_image_form(make_images(4)),
                           headers=csrf_headers, content_type="multipart/form-data")
    job_id = response.get_json()["job"]["id"]
    cancel = client.post(f"/api/jobs/{job_id}/cancel", headers=csrf_headers)
    assert cancel.status_code == 200
    job = _wait_for_job(client, job_id)
    assert job["status"] in ("done", "cancelled")


def test_demo_endpoint_builds_a_video(client, csrf_headers, fake_render, fake_tts):
    response = client.post("/api/demo", headers=csrf_headers)
    assert response.status_code == 202
    job = _wait_for_job(client, response.get_json()["job"]["id"])
    assert job["status"] == "done", job.get("error")
    assert job["result"]["duration"] == 22.4


def test_request_entity_too_large_is_json(client, csrf_headers, monkeypatch):
    monkeypatch.setitem(client.application.config, "MAX_CONTENT_LENGTH", 200)
    response = client.post("/api/settings", json={"pad": "x" * 2000}, headers=csrf_headers)
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "too_large"


# --------------------------------------------------------------------------- #
# Music library routes
# --------------------------------------------------------------------------- #
def test_music_upload_and_delete(client, csrf_headers, make_wav, tmp_path, monkeypatch):
    monkeypatch.setattr(music, "MUSIC_DIR", tmp_path)
    monkeypatch.setattr(music, "INDEX_PATH", tmp_path / "index.json")

    wav = make_wav("piano.wav", seconds=3.0)
    response = client.post("/api/music",
                           data={"file": (io.BytesIO(wav.read_bytes()), "piano.wav")},
                           headers=csrf_headers, content_type="multipart/form-data")
    assert response.status_code == 200
    track = response.get_json()["track"]

    listing = client.get("/api/music").get_json()
    assert len(listing["tracks"]) == 1
    assert listing["rights_notice"]

    stream = client.get(f"/media/music/{track['id']}")
    assert stream.status_code == 200
    stream.close()  # release the file handle before deleting on Windows

    removed = client.delete(f"/api/music/{track['id']}", headers=csrf_headers)
    assert removed.status_code == 200
    assert client.get("/api/music").get_json()["tracks"] == []


def test_corrupt_music_upload_is_rejected(client, csrf_headers, tmp_path, monkeypatch):
    monkeypatch.setattr(music, "MUSIC_DIR", tmp_path)
    monkeypatch.setattr(music, "INDEX_PATH", tmp_path / "index.json")
    response = client.post("/api/music",
                           data={"file": (io.BytesIO(b"not audio" * 100), "bad.mp3")},
                           headers=csrf_headers, content_type="multipart/form-data")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_music"


def test_basic_auth_gate_when_deployment_credentials_are_set(monkeypatch):
    import base64

    monkeypatch.setattr(app_module, "AUTH_ENABLED", True)
    monkeypatch.setattr(app_module, "APP_USERNAME", "studio")
    monkeypatch.setattr(app_module, "APP_PASSWORD", "s3cret-pass")
    client = app_module.create_app().test_client()

    denied = client.get("/")
    assert denied.status_code == 401
    assert denied.headers["WWW-Authenticate"].startswith("Basic")

    good = base64.b64encode(b"studio:s3cret-pass").decode()
    assert client.get("/", headers={"Authorization": f"Basic {good}"}).status_code == 200

    bad = base64.b64encode(b"studio:wrong").decode()
    assert client.get("/", headers={"Authorization": f"Basic {bad}"}).status_code == 401


def test_remote_clients_are_refused_without_credentials():
    client = app_module.create_app().test_client()
    response = client.get("/api/health",
                          environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "forbidden"
