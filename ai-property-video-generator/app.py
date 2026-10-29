"""AI Property Video Generator — Flask application.

Local-first by default: without credentials the server binds to 127.0.0.1 and
only accepts loopback clients. For a hosted deployment set APP_USERNAME and
APP_PASSWORD; every request then requires HTTP Basic authentication, and a
reverse proxy should terminate HTTPS. Mutating API calls always require a CSRF
token, and no API key is ever serialised to the browser.

Start with:  python app.py   (then open http://127.0.0.1:5000)
Production:  waitress-serve --listen=0.0.0.0:8000 wsgi:app
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import sys
import secrets
import threading
import time
import traceback
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from config.providers import TTS_ENGINES, public_catalog, sapi_voices
from config.settings import store
from services import ffmpeg_tools, music
from services.errors import (
    AppError,
    CancelledError,
    ConflictError,
    ImageError,
    NotFoundError,
    SecurityError,
    TTSError,
    ValidationError,
)
from services.images import normalise_image
from services.jobs import jobs, render_lock
from services.logging_setup import get_logger, setup_logging
from services.script_generator import (
    extract_json,
    generate_script,
    test_llm_connection,
    validate_script,
    word_budget,
)
from services.storage import (
    CONFIG_DIR,
    LOG_DIR,
    SAMPLE_DIR,
    UPLOAD_DIR,
    VIDEO_DIR,
    cleanup,
    ensure_dirs,
    human_size,
    is_id,
    new_id,
    safe_join,
    slugify,
)
from services.template_script import generate_template_script
from services.timeline import build_timeline
from services.tts import preview as tts_preview
from services.tts import synthesize, test_engine
from services.validation import (
    load_property_json,
    parse_property,
    validate_image_selection,
)
from services.video import RenderRequest, render_video

# Windows consoles often default to cp1252, which cannot encode '→' or '✓'.
# Reconfiguring keeps startup and progress printing from crashing.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):  # pragma: no cover - non-standard streams
    pass

load_dotenv(Path(__file__).resolve().parent / ".env")

ensure_dirs()
logger = setup_logging(LOG_DIR)
DEFAULTS = json.loads((CONFIG_DIR / "defaults.json").read_text(encoding="utf-8"))

ALLOWED_LOCAL_ADDRESSES = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}
CSRF_HEADER = "X-CSRF-Token"
PREVIEW_TTL_SECONDS = 3600

# Deployment credentials are optional. When both are set the app can serve a
# network without exposing key-spending endpoints to anonymous visitors.
APP_USERNAME = os.environ.get("APP_USERNAME", "").strip()
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
AUTH_ENABLED = bool(APP_USERNAME and APP_PASSWORD)


def _basic_auth_ok() -> bool:
    header = request.headers.get("Authorization", "")
    scheme, _, payload = header.partition(" ")
    if scheme.lower() != "basic" or not payload:
        return False
    try:
        decoded = base64.b64decode(payload, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    username, separator, password = decoded.partition(":")
    if not separator:
        return False
    return (secrets.compare_digest(username, APP_USERNAME)
            and secrets.compare_digest(password, APP_PASSWORD))


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #
def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["SECRET_KEY"] = store.session_key()
    app.config["MAX_CONTENT_LENGTH"] = int(DEFAULTS["limits"]["max_request_mb"]) * 1024 * 1024
    app.config["JSON_SORT_KEYS"] = False
    app.config["TEMPLATES_AUTO_RELOAD"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("APP_HTTPS") == "1"

    # Behind a reverse proxy (Render/Railway/Fly/nginx), trust one forwarded hop
    # so remote addresses and HTTPS URLs are reported correctly.
    if os.environ.get("TRUST_PROXY") == "1":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # -- guards ------------------------------------------------------------ #
    def local_only(view):
        """Loopback-only unless deployment credentials turn the app public."""
        @wraps(view)
        def wrapper(*args, **kwargs):
            address = (request.remote_addr or "").strip()
            if address not in ALLOWED_LOCAL_ADDRESSES and not AUTH_ENABLED:
                raise SecurityError("This app only accepts requests from this computer.")
            return view(*args, **kwargs)

        return wrapper

    def require_auth():
        if AUTH_ENABLED and not _basic_auth_ok():
            return ("Authentication required.", 401,
                    {"WWW-Authenticate": 'Basic realm="Property Video Studio", charset="UTF-8"'})
        return None

    def csrf_protect():
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return
        if not request.path.startswith("/api/"):
            return
        expected = session.get("_csrf")
        supplied = request.headers.get(CSRF_HEADER) or request.form.get("_csrf") or ""
        if not expected or not secrets.compare_digest(str(supplied), str(expected)):
            raise SecurityError("Your session token expired. Reload the page and try again.")

    app.before_request(require_auth)
    app.before_request(csrf_protect)

    def csrf_token() -> str:
        token = session.get("_csrf")
        if not token:
            token = secrets.token_urlsafe(24)
            session["_csrf"] = token
        return token

    def page_context(**extra) -> dict:
        return {
            "csrf_token": csrf_token(),
            "catalog": public_catalog(),
            "settings": store.public(),
            "defaults": DEFAULTS,
            "limits": DEFAULTS["limits"],
            "secrets": store.all_secret_status(),
            "has_llm_key": bool(store.get_secret(
                "gemini" if store.public()["llm"]["provider"] == "gemini" else "openai_compat")),
            **extra,
        }

    # -- pages ------------------------------------------------------------- #
    @app.get("/")
    @local_only
    def index():
        return render_template("index.html", **page_context(active_page="create"))

    @app.get("/settings")
    @local_only
    def settings_page():
        return render_template("settings.html", **page_context(active_page="settings"))

    # -- health ------------------------------------------------------------ #
    @app.get("/api/health")
    @local_only
    def api_health():
        checks = []
        try:
            version = ffmpeg_tools.version()
            checks.append({"name": "FFmpeg", "ok": True, "detail": version})
        except AppError as exc:
            checks.append({"name": "FFmpeg", "ok": False, "detail": exc.message})
        checks.append({
            "name": "ffprobe",
            "ok": bool(ffmpeg_tools.ffprobe_path()),
            "detail": ffmpeg_tools.ffprobe_path() or "not found (using ffmpeg fallback)",
        })
        checks.append({"name": "Disk space", "ok": True,
                       "detail": f"{ffmpeg_tools.disk_free_mb(VIDEO_DIR)} MB free"})
        checks.append({"name": "Offline voices", "ok": bool(sapi_voices()),
                       "detail": f"{len(sapi_voices())} Windows voice(s)"})
        return jsonify({
            "ok": all(check["ok"] for check in checks if check["name"] != "ffprobe"),
            "checks": checks,
            "server_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

    # -- settings ---------------------------------------------------------- #
    @app.get("/api/settings")
    @local_only
    def get_settings():
        return jsonify({
            "public": store.public(),
            "secrets": store.all_secret_status(),
            "catalog": public_catalog(),
        })

    @app.post("/api/settings")
    @local_only
    def save_settings():
        payload = _json_body()
        updated = store.update_public(payload)
        # Register any newly configured key so it is scrubbed from logs.
        for name in ("gemini", "openai_compat", "openai_tts", "elevenlabs"):
            value = store.get_secret(name)
            if value:
                from services.logging_setup import register_secret

                register_secret(value)
        return jsonify({"ok": True, "settings": updated,
                        "message": "Settings saved."})

    @app.post("/api/settings/secret")
    @local_only
    def save_secret():
        payload = _json_body()
        name = str(payload.get("name") or "").strip()
        value = str(payload.get("value") or "")
        status = store.set_secret(name, value)
        from services.logging_setup import register_secret

        register_secret(value)
        return jsonify({"ok": True, "secret": status,
                        "message": f"Key saved for {status['label']}."})

    @app.delete("/api/settings/secret/<name>")
    @local_only
    def remove_secret(name: str):
        status = store.delete_secret(name)
        message = ("Key removed." if not status["configured"]
                   else "The key still comes from your .env file, so it cannot be removed here.")
        return jsonify({"ok": True, "secret": status, "message": message})

    @app.post("/api/settings/test")
    @local_only
    def test_connection():
        payload = _json_body()
        kind = str(payload.get("kind") or "llm")
        if kind == "tts":
            result = test_engine(str(payload.get("engine") or store.public()["tts"]["engine"]),
                                 payload.get("voice") or None)
        else:
            result = test_llm_connection(
                store,
                provider_id=payload.get("provider") or None,
                model=payload.get("model") or None,
                base_url=payload.get("base_url") if "base_url" in payload else None,
                api_key=payload.get("api_key") or None,
            )
        logger.info("Connection test (%s): ok=%s code=%s", kind, result.get("ok"),
                    result.get("code"))
        return jsonify(result)

    # -- music library ----------------------------------------------------- #
    @app.get("/api/music")
    @local_only
    def list_music():
        return jsonify({
            "tracks": music.list_tracks(),
            "usage": music.library_usage(),
            "rights_notice": music.RIGHTS_NOTICE,
            "accepted": sorted({".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}),
            "max_mb": DEFAULTS["limits"]["max_audio_mb"],
        })

    @app.post("/api/music")
    @local_only
    def upload_music():
        file = request.files.get("file")
        if not file or not file.filename:
            raise ValidationError("Choose an audio file to add to the library.", field="music")
        data = file.read()
        music.validate_upload_size(data, DEFAULTS["limits"])
        track = music.add_to_library(file.filename, data, DEFAULTS["limits"])
        return jsonify({"ok": True, "track": track,
                        "message": f"Added '{track['name']}' to the music library."})

    @app.delete("/api/music/<track_id>")
    @local_only
    def delete_music(track_id: str):
        music.delete_track(track_id)
        return jsonify({"ok": True, "message": "Track removed from the library."})

    @app.get("/media/music/<track_id>")
    @local_only
    def serve_music(track_id: str):
        path = music.track_path(track_id)
        return send_file(path, conditional=True)

    # -- script generation ------------------------------------------------- #
    @app.post("/api/script")
    @local_only
    def api_script():
        payload = _json_body()
        facts = parse_property(payload.get("property") or {})
        image_count = int(payload.get("image_count") or 0)
        limits = DEFAULTS["limits"]
        if not limits["min_images"] <= image_count <= limits["max_images"]:
            raise ValidationError(
                f"Select {limits['min_images']}–{limits['max_images']} photos first.",
                field="images",
            )
        tone = str(payload.get("tone") or store.public()["video"]["tone"])
        if tone not in DEFAULTS["script"]["tones"]:
            tone = DEFAULTS["script"]["tone"]

        if payload.get("force_template"):
            budget = word_budget(image_count)
            data = generate_template_script(facts, image_count,
                                            max_words_per_scene=budget["scene"],
                                            max_words_intro=budget["intro"],
                                            max_words_cta=budget["cta"], tone=tone)
            result = {
                "data": data, "source": "template", "provider": None, "model": None,
                "attempts": 0,
                "fallback_reason": "Template mode was selected, so the built-in script was used.",
                "warnings": [], "is_ai": False,
            }
        else:
            result = generate_script(facts, image_count).to_public()

        estimate = build_timeline(result["data"], [Path(f"img{i}.jpg") for i in range(image_count)],
                                  defaults=DEFAULTS)
        return jsonify({
            "ok": True,
            "script": result,
            "budget": word_budget(image_count),
            "estimate": {"total": estimate.total,
                         "scene_count": len(estimate.scenes),
                         "warnings": estimate.warnings},
        })

    # -- narration preview -------------------------------------------------- #
    @app.post("/api/narration/preview")
    @local_only
    def api_narration_preview():
        payload = _json_body()
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValidationError("Generate a script first, then preview the narration.",
                                  field="text")
        preview_dir = UPLOAD_DIR / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        _cleanup_previews(preview_dir)
        out_dir = preview_dir / new_id()
        out_dir.mkdir(parents=True, exist_ok=True)
        clip_path, result = tts_preview(text, out_dir)
        return jsonify({
            "ok": True,
            "url": f"/media/preview/{out_dir.name}",
            "narration": result.to_public(),
            "message": _narration_message(result),
        })

    @app.get("/media/preview/<preview_id>")
    @local_only
    def serve_preview(preview_id: str):
        if not is_id(preview_id):
            raise NotFoundError("That preview is not available.")
        preview_dir = safe_join(UPLOAD_DIR / "previews", preview_id)
        candidates = sorted(preview_dir.glob("narration_*")) if preview_dir.is_dir() else []
        if not candidates:
            raise NotFoundError("That narration preview has expired. Generate it again.")
        return send_file(candidates[0], conditional=True)

    # -- generation -------------------------------------------------------- #
    @app.post("/api/generate")
    @local_only
    def api_generate():
        form = request.form
        facts = parse_property(form)

        files = [file for file in request.files.getlist("images") if file and file.filename]
        validate_image_selection([file.filename for file in files], DEFAULTS["limits"])
        images_payload = [(file.filename, file.read()) for file in files]
        for filename, data in images_payload:
            if len(data) > int(DEFAULTS["limits"]["max_image_mb"]) * 1024 * 1024:
                raise ImageError(f"'{filename}' is larger than "
                                 f"{DEFAULTS['limits']['max_image_mb']} MB.",
                                 hint="Compress it and try again.")

        enable_narration = _truthy(form.get("enable_narration"))
        music_mode = (form.get("music_mode") or "none").strip().lower()
        music_file_payload = None
        music_id = None
        save_to_library = _truthy(form.get("save_to_library"))

        if music_mode == "library":
            music_id = (form.get("music_id") or "").strip()
            if not music_id:
                raise ValidationError("Choose a track from the library, or select 'No music'.",
                                      field="music")
            music.get_track(music_id)  # raises a friendly NotFoundError
        elif music_mode == "upload":
            upload = request.files.get("music_file")
            if not upload or not upload.filename:
                raise ValidationError("Choose a music file, or switch background music off.",
                                      field="music")
            data = upload.read()
            music.validate_upload_size(data, DEFAULTS["limits"])
            music_file_payload = (upload.filename, data)
        elif music_mode != "none":
            raise ValidationError("Unknown background music option.", field="music")

        try:
            raw_volume = float(form.get("music_volume", "") or
                               store.public()["video"]["music_volume"])
            # The UI sends a 0-100 slider; accept either scale defensively.
            volume = raw_volume / 100.0 if raw_volume > 1.0 else raw_volume
        except (TypeError, ValueError):
            volume = float(store.public()["video"]["music_volume"])
        volume = max(0.0, min(1.0, volume))

        script_payload = None
        raw_script = form.get("script_json")
        if raw_script:
            try:
                script_payload = json.loads(raw_script)
            except json.JSONDecodeError:
                script_payload = None

        job = _begin_job()
        payload = {
            "facts": facts,
            "images": images_payload,
            "enable_narration": enable_narration,
            "force_template": _truthy(form.get("force_template")),
            "script_payload": script_payload,
            "music_mode": music_mode,
            "music_id": music_id,
            "music_file": music_file_payload,
            "save_to_library": save_to_library,
            "music_volume": volume,
        }
        _start_worker(app, job, payload)
        return jsonify({"ok": True, "job": job.to_public()}), 202

    @app.get("/api/sample")
    @local_only
    def api_sample():
        sample_path = SAMPLE_DIR / "property_sample.json"
        if not sample_path.is_file():
            raise NotFoundError("The sample property file is missing.")
        logger.info("Sample property data served.")
        return jsonify(json.loads(sample_path.read_text(encoding="utf-8")))

    @app.post("/api/demo")
    @local_only
    def api_demo():
        """Build a video from the shipped synthetic sample data (evaluator shortcut)."""
        sample_json = SAMPLE_DIR / "property_sample.json"
        if not sample_json.is_file():
            raise NotFoundError("The sample property file is missing.")
        facts = load_property_json(sample_json)
        image_dir = SAMPLE_DIR / "images"
        images = sorted([path for path in image_dir.glob("*") if path.suffix.lower() in
                         (".jpg", ".jpeg", ".png", ".webp")])
        if len(images) < int(DEFAULTS["limits"]["min_images"]):
            raise NotFoundError("The sample images are missing from sample_data/images.")
        payload = {
            "facts": facts,
            "images": [(path.name, path.read_bytes()) for path in images],
            "enable_narration": True,
            "force_template": not bool(store.get_secret("gemini")),
            "script_payload": None,
            "music_mode": "none",
            "music_id": None,
            "music_file": None,
            "save_to_library": False,
            "music_volume": float(store.public()["video"]["music_volume"]),
            "sample": True,
        }
        job = _begin_job()
        _start_worker(app, job, payload)
        return jsonify({"ok": True, "job": job.to_public()}), 202

    # -- job status and media ---------------------------------------------- #
    @app.get("/api/jobs/<job_id>")
    @local_only
    def job_status(job_id: str):
        job = _require_job(job_id)
        return jsonify(job.to_public())

    @app.post("/api/jobs/<job_id>/cancel")
    @local_only
    def job_cancel(job_id: str):
        job = _require_job(job_id)
        cancelled = job.cancel()
        return jsonify({"ok": True, "cancelled": cancelled,
                        "message": "Cancelling after the current step…" if cancelled
                                   else "This job has already finished."})

    @app.get("/video/<job_id>")
    @local_only
    def serve_video(job_id: str):
        job = _require_job(job_id)
        path = _result_path(job, "video")
        return send_file(path, mimetype="video/mp4", conditional=True)

    @app.get("/download/<job_id>")
    @local_only
    def download_video(job_id: str):
        job = _require_job(job_id)
        path = _result_path(job, "video")
        filename = job.result.get("filename", "property-video.mp4") if job.result else "video.mp4"
        return send_file(path, mimetype="video/mp4", as_attachment=True,
                         download_name=filename, conditional=True)

    @app.get("/poster/<job_id>")
    @local_only
    def serve_poster(job_id: str):
        job = _require_job(job_id)
        path = _result_path(job, "poster", required=False)
        if not path:
            raise NotFoundError("No poster is available for this video.")
        return send_file(path, mimetype="image/jpeg", conditional=True)

    # -- error handling ---------------------------------------------------- #
    @app.errorhandler(AppError)
    def handle_app_error(error: AppError):
        log_detail = error.log_detail or error.message
        if error.code == "cancelled":
            logger.info("Request cancelled: %s", request.path)
        else:
            logger.warning("%s %s → %s (%s)", request.method, request.path, error.code,
                           log_detail[:500])
        if request.path.startswith("/api/") or request.path.startswith("/media/") \
                or request.path.startswith("/video/") or request.path.startswith("/download/"):
            return jsonify({"error": error.to_public()}), error.http_status
        return render_template("error.html", error=error.to_public(),
                               **page_context(active_page="")), error.http_status

    @app.errorhandler(413)
    def handle_too_large(_error):
        limit = DEFAULTS["limits"]["max_request_mb"]
        payload = {"code": "too_large",
                   "message": f"The upload is larger than the {limit} MB request limit.",
                   "hint": "Use fewer or smaller photos (15 MB each), then try again.",
                   "retryable": False}
        if request.path.startswith("/api/"):
            return jsonify({"error": payload}), 413
        return render_template("error.html", error=payload, **page_context()), 413

    @app.errorhandler(404)
    def handle_404(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": {"code": "not_found",
                                      "message": "That endpoint does not exist.",
                                      "retryable": False}}), 404
        return render_template("error.html", error={
            "code": "not_found", "message": "That page does not exist.",
            "hint": "Return to the generator to continue."}, **page_context()), 404

    @app.errorhandler(Exception)
    def handle_unexpected(error: Exception):
        if isinstance(error, AppError):
            return handle_app_error(error)
        if isinstance(error, HTTPException):
            return error
        logger.error("Unhandled error on %s: %s\n%s", request.path, error,
                     traceback.format_exc(limit=6))
        payload = {"code": "server_error",
                   "message": "Something went wrong on the server.",
                   "hint": "Check the terminal window for details, then try again.",
                   "retryable": True}
        if request.path.startswith("/api/"):
            return jsonify({"error": payload}), 500
        return render_template("error.html", error=payload, **page_context()), 500

    return app


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _json_body() -> dict:
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValidationError("Expected a JSON object.")
    return payload


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _require_job(job_id: str):
    if not is_id(job_id):
        raise NotFoundError("That video could not be found.")
    job = jobs.get(job_id)
    if job is None:
        raise NotFoundError("That video could not be found. It may have expired.")
    return job


def _result_path(job, key: str, required: bool = True) -> Path | None:
    """Resolve a private media path for a finished job.

    Paths live on ``job.media`` rather than ``job.result`` so they can never leak
    into an API response; the UI only ever sees opaque /video/<id> URLs.
    """
    stored = (job.media or {}).get(key)
    if not stored:
        if not required:
            return None
        raise NotFoundError("This video is not ready yet.")
    path = Path(stored)
    if not path.is_file():
        raise NotFoundError("The video file is no longer on disk.")
    return path


def _begin_job():
    if not render_lock.acquire(timeout=0.05):
        raise ConflictError("A video is already rendering.",
                            hint="Wait for the current render to finish, then start again.")
    try:
        job = jobs.create(UPLOAD_DIR / "jobs")
    except Exception:
        render_lock.release()
        raise
    return job


def _start_worker(app: Flask, job, payload: dict) -> None:
    thread = threading.Thread(target=_worker, args=(app, job, payload), daemon=True,
                              name=f"render-{job.id}")
    thread.start()


def _worker(app: Flask, job, payload: dict) -> None:
    """Run the full pipeline, updating stages only as real work completes."""
    started = time.time()
    try:
        job.begin()
        job.complete_stage("validate", "Property details and uploads accepted")

        # ---- images ------------------------------------------------------ #
        job.set_stage("images", "running", "Normalising photos")
        image_dir = job.work_dir / "images"
        thumbs_dir = job.work_dir / "thumbs"
        image_paths: list[Path] = []
        for index, (filename, data) in enumerate(payload["images"], start=1):
            destination = image_dir / f"image{index:02d}.jpg"
            info = normalise_image(data, destination, DEFAULTS["limits"])
            image_paths.append(Path(info["path"]))
            job.set_stage("images", "running",
                          f"Prepared photo {index} of {len(payload['images'])}")
        job.complete_stage("images", f"{len(image_paths)} photos ready")

        facts = payload["facts"]

        # ---- script ------------------------------------------------------ #
        job.set_stage("script", "running", "Writing the script")
        script_source = "template"
        script_meta: dict = {}
        script_data = payload.get("script_payload")
        reused = False
        if script_data and not payload.get("force_template"):
            try:
                budget = word_budget(len(image_paths))
                validated = validate_script(extract_json(json.dumps(script_data)),
                                            len(image_paths), budget)
                from services import fact_guard

                if not fact_guard.check_script(validated, facts):
                    script_data = validated
                    reused = True
                    script_source = "ai"
                    script_meta = {"reused": True}
            except AppError:
                reused = False

        if not reused:
            if payload.get("force_template"):
                budget = word_budget(len(image_paths))
                script_data = generate_template_script(
                    facts, len(image_paths),
                    max_words_per_scene=budget["scene"],
                    max_words_intro=budget["intro"],
                    max_words_cta=budget["cta"],
                )
                script_source = "template"
                script_meta = {"fallback_reason": "Template mode was selected for this video."}
            else:
                result = generate_script(facts, len(image_paths))
                script_data = result.data
                script_source = result.source
                script_meta = {
                    "provider": result.provider, "model": result.model,
                    "attempts": result.attempts,
                    "fallback_reason": result.fallback_reason,
                }
                for warning in result.warnings:
                    job.add_warning(warning)

        job.complete_stage("script", "AI script ready" if script_source == "ai"
                           else "Template script ready")
        if script_meta.get("fallback_reason"):
            job.add_warning(script_meta["fallback_reason"])

        # ---- narration --------------------------------------------------- #
        narration_clips = None
        narration_durations = None
        narration_meta = None
        if not payload["enable_narration"]:
            job.skip_stage("narration", "Voice-over is off")
        else:
            job.set_stage("narration", "running", "Generating narration")
            texts = _narration_texts(script_data)
            narration_dir = job.work_dir / "narration"
            try:
                tts_result = synthesize(texts, narration_dir, settings_store=store)
            except TTSError as exc:
                job.set_stage("narration", "failed", exc.message)
                raise
            narration_clips = list(tts_result.clips)
            narration_durations = list(tts_result.durations)
            narration_meta = tts_result.to_public()
            for warning in tts_result.warnings:
                job.add_warning(warning)
            job.complete_stage("narration", _narration_message(tts_result))

        # ---- music ------------------------------------------------------- #
        music_path = None
        music_name = ""
        if payload["music_mode"] == "none":
            job.skip_stage("music", "No background music")
        else:
            job.set_stage("music", "running", "Preparing music")
            if payload["music_mode"] == "library":
                track = music.get_track(payload["music_id"])
                music_path = music.track_path(track.id)
                music_name = track.name
            else:
                filename, data = payload["music_file"]
                music_dir = job.work_dir / "music"
                music_path = music.save_temp_track(filename, data, music_dir,
                                                   DEFAULTS["limits"])
                music_name = filename
                if payload["save_to_library"]:
                    try:
                        music.add_to_library(filename, data, DEFAULTS["limits"])
                        job.add_warning(f"'{filename}' was also saved to the music library.")
                    except AppError as exc:
                        job.add_warning(f"Music was used for this video, but it could not be "
                                        f"added to the library: {exc.message}")
            job.complete_stage("music", f"Using '{music_name}'")

        # ---- render ------------------------------------------------------ #
        if job.cancelled:
            raise CancelledError()
        job.set_stage("render", "running", "Rendering video")

        def progress(stage_id: str, detail: str) -> None:
            if stage_id == "verify":
                job.complete_stage("render", "Video assembled")
                job.set_stage("verify", "running", detail)
            else:
                job.set_stage("render", "running", detail)

        output = job.work_dir / "output" / f"{slugify(facts.name)}-promo.mp4"
        result = render_video(RenderRequest(
            facts=facts,
            script=script_data,
            images=image_paths,
            work_dir=job.work_dir,
            output_path=output,
            narration_clips=narration_clips,
            narration_durations=narration_durations,
            music_path=music_path,
            music_name=music_name,
            music_volume=float(payload["music_volume"]),
            progress=progress,
            should_cancel=lambda: job.cancelled,
            defaults=DEFAULTS,
        ))
        for warning in result.get("warnings", []):
            job.add_warning(warning)

        elapsed = round(time.time() - started, 1)
        job.complete_stage("verify", "MP4 decoded cleanly")
        job.complete_stage("ready", f"Finished in {elapsed}s")

        job.media = {"video": result["path"],
                     "poster": result.get("poster_path", "")}
        job.succeed({
            "filename": f"{slugify(facts.name)}-property-video.mp4",
            "video_url": f"/video/{job.id}",
            "download_url": f"/download/{job.id}",
            "poster_url": f"/poster/{job.id}" if result.get("poster_path") else None,
            "duration": result["duration"],
            "size_bytes": result["size_bytes"],
            "size_label": human_size(result["size_bytes"]),
            "resolution": result["resolution"],
            "has_audio": result["has_audio"],
            "audio_codec": result["audio_codec"],
            "music_used": result["music_used"],
            "music_name": result["music_name"],
            "narration": narration_meta,
            "script": {"source": script_source, **script_meta},
            "elapsed_seconds": elapsed,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        logger.info("Job %s finished in %ss (%s, %s)", job.id, elapsed,
                    script_source, human_size(result["size_bytes"]))
        jobs.cleanup_workdir(job)

    except CancelledError:
        logger.info("Job %s cancelled by user.", job.id)
        job.fail({"code": "cancelled", "message": "Generation was cancelled.",
                  "retryable": True})
        jobs.cleanup_workdir(job)
    except AppError as exc:
        logger.warning("Job %s failed: %s (%s)", job.id, exc.code,
                       (exc.log_detail or exc.message)[:500])
        job.fail(exc.to_public())
        jobs.cleanup_workdir(job)
    except Exception as exc:  # unexpected bug: keep the input data, expose no internals
        logger.error("Job %s crashed: %s\n%s", job.id, exc, traceback.format_exc(limit=6))
        job.fail({"code": "server_error",
                  "message": "The video could not be generated because of an unexpected error.",
                  "hint": "Your details are still in the form. Try again, or check the "
                          "terminal log for details.",
                  "retryable": True})
    finally:
        render_lock.release()


def _narration_texts(script: dict) -> list[str]:
    texts = [str(script.get("intro_narration") or "")]
    texts += [str(scene.get("narration") or "") for scene in script.get("scenes", [])]
    texts.append(str(script.get("cta_narration") or ""))
    return [" ".join(text.split()) for text in texts]


def _narration_message(result) -> str:
    label = TTS_ENGINES.get(result.engine, {}).get("label", result.engine)
    if result.fallback_used:
        primary = TTS_ENGINES.get(result.primary_engine or "", {}).get(
            "label", result.primary_engine or "the preferred engine")
        return (f"Narration used {label} because {primary} could not be used.")
    return f"Narration generated with {label}."


def _cleanup_previews(preview_dir: Path) -> None:
    cutoff = time.time() - PREVIEW_TTL_SECONDS
    try:
        for entry in preview_dir.iterdir():
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                cleanup(entry)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
app = create_app()


if __name__ == "__main__":
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "5000"))
    if host not in ALLOWED_LOCAL_ADDRESSES:
        if AUTH_ENABLED:
            print("\nHTTP Basic authentication is enabled for this deployment.")
            print("Terminate HTTPS at a reverse proxy; do not serve this over plain HTTP.\n")
        else:
            print("\nWARNING: no APP_USERNAME/APP_PASSWORD set — remote clients are refused.")
            print("Set both credentials before exposing the app beyond this computer.\n")
    print(f"\n  AI Property Video Generator → http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)
