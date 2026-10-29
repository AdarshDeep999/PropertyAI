"""Settings and secret storage.

Design notes
------------
* Non-secret choices live in ``config/settings.json`` (human readable).
* Secrets live apart in ``config/secrets.json`` and are encrypted with Windows
  DPAPI (``CryptProtectData``) when available, so the ciphertext is tied to the
  current Windows user account. Where DPAPI is unavailable the file is written
  with owner-only permissions instead and the app logs a warning.
* The frontend never receives a secret: ``secret_status`` returns only whether a
  key exists, where it came from, and a masked hint.
* Keys can also come from the environment (``.env``); those are read-only in the
  UI, because the app will not write to your environment.

Known limitation (documented in README): local secret storage protects against
casual disclosure, not against malware already running as your Windows user.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import secrets as py_secrets
import sys
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Any

from config.providers import LLM_PROVIDERS, TTS_ENGINES
from services.errors import SettingsError
from services.logging_setup import get_logger, register_secret
from services.storage import CONFIG_DIR, write_bytes_atomic

log = get_logger("settings")

PUBLIC_PATH = CONFIG_DIR / "settings.json"
SECRETS_PATH = CONFIG_DIR / "secrets.json"
SESSION_KEY_PATH = CONFIG_DIR / "session.key"

SECRET_ENV_VARS = {
    "gemini": "GEMINI_API_KEY",
    "openai_compat": "OPENAI_COMPAT_API_KEY",
    "openai_tts": "OPENAI_TTS_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
}
SECRET_LABELS = {
    "gemini": "Google Gemini",
    "openai_compat": "OpenAI-compatible LLM",
    "openai_tts": "OpenAI text-to-speech",
    "elevenlabs": "ElevenLabs",
}

DEFAULT_PUBLIC: dict[str, Any] = {
    "llm": {
        "provider": "gemini",
        "model": "gemini-3.5-flash",
        "base_url": "",
        "force_template": False,
    },
    "tts": {
        "engine": "edge",
        "fallback_engine": "windows_sapi",
        "voice": "en-US-AriaNeural",
        "fallback_voice": "Microsoft Zira Desktop",
        "speed": 1.0,
    },
    "video": {"music_volume": 0.30, "tone": "warm"},
}


# --------------------------------------------------------------------------- #
# Windows DPAPI helpers (pure ctypes: no pywin32 dependency)
# --------------------------------------------------------------------------- #
class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_of(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data, len(data) or 1)
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def dpapi_available() -> bool:
    return sys.platform == "win32"


def _dpapi_protect(data: bytes) -> bytes:
    source, _keep = _blob_of(data)
    target = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(  # type: ignore[attr-defined]
        ctypes.byref(source), ctypes.c_wchar_p("apvg-local-secrets"), None, None, None,
        0x1,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(target),
    )
    if not ok:
        raise OSError(f"CryptProtectData failed ({ctypes.GetLastError()})")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)  # type: ignore[attr-defined]


def _dpapi_unprotect(data: bytes) -> bytes:
    source, _keep = _blob_of(data)
    target = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(  # type: ignore[attr-defined]
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(target)
    )
    if not ok:
        raise OSError(f"CryptUnprotectData failed ({ctypes.GetLastError()})")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
class SettingsStore:
    def __init__(self, public_path: Path = PUBLIC_PATH, secrets_path: Path = SECRETS_PATH):
        self.public_path = public_path
        self.secrets_path = secrets_path
        self._lock = threading.RLock()
        self._secrets: dict[str, str] | None = None

    # -- public settings ---------------------------------------------------- #
    def public(self) -> dict[str, Any]:
        with self._lock:
            data = _read_json(self.public_path, {})
        return _merge_defaults(DEFAULT_PUBLIC, data)

    def update_public(self, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise SettingsError("Settings payload must be an object.")
        current = self.public()
        cleaned = _validate_public_patch(patch, current)
        merged = _merge_defaults(current, cleaned)
        with self._lock:
            write_bytes_atomic(self.public_path, json.dumps(merged, indent=2).encode("utf-8"),
                               mode=0o600)
        return merged

    # -- secrets ------------------------------------------------------------ #
    def _load_secrets(self) -> dict[str, str]:
        with self._lock:
            if self._secrets is not None:
                return dict(self._secrets)
            data = _read_json(self.secrets_path, {})
            protection = data.get("protection", "plain")
            payload = data.get("payload", "")
            secrets: dict[str, str] = {}
            if payload:
                try:
                    raw = base64.b64decode(payload.encode("ascii"))
                    if protection == "dpapi":
                        raw = _dpapi_unprotect(raw)
                    decoded = json.loads(raw.decode("utf-8"))
                    if isinstance(decoded, dict):
                        secrets = {str(k): str(v) for k, v in decoded.items() if v}
                except Exception as exc:
                    # A corrupted or foreign-user secrets file must not brick the app.
                    log.warning("Stored secrets could not be read (%s); starting empty.", exc)
                    secrets = {}
            for value in secrets.values():
                register_secret(value)
            self._secrets = secrets
            return dict(secrets)

    def _save_secrets(self, secrets: dict[str, str]) -> None:
        raw = json.dumps(secrets).encode("utf-8")
        protection = "plain"
        if dpapi_available():
            try:
                raw = _dpapi_protect(raw)
                protection = "dpapi"
            except OSError as exc:
                log.warning("DPAPI encryption unavailable (%s); storing with file permissions only.", exc)
        document = {
            "version": 1,
            "protection": protection,
            "payload": base64.b64encode(raw).decode("ascii"),
        }
        with self._lock:
            write_bytes_atomic(self.secrets_path, json.dumps(document, indent=2).encode("utf-8"),
                               mode=0o600)
            self._secrets = dict(secrets)
        for value in secrets.values():
            register_secret(value)

    def get_secret(self, name: str) -> str | None:
        if name not in SECRET_ENV_VARS:
            return None
        stored = self._load_secrets().get(name)
        if stored:
            return stored
        env_value = os.environ.get(SECRET_ENV_VARS[name], "").strip()
        return env_value or None

    def secret_status(self, name: str) -> dict[str, Any]:
        stored = self._load_secrets().get(name)
        env_value = os.environ.get(SECRET_ENV_VARS.get(name, ""), "").strip()
        value = stored or env_value or ""
        source = "settings" if stored else ("environment" if env_value else None)
        return {
            "name": name,
            "label": SECRET_LABELS.get(name, name),
            "configured": bool(value),
            "masked": _mask(value) if value else "",
            "source": source,
            "env_var": SECRET_ENV_VARS.get(name),
            "removable": bool(stored),
        }

    def all_secret_status(self) -> dict[str, dict[str, Any]]:
        return {name: self.secret_status(name) for name in SECRET_ENV_VARS}

    def set_secret(self, name: str, value: str) -> dict[str, Any]:
        if name not in SECRET_ENV_VARS:
            raise SettingsError("Unknown credential.", field="name")
        value = (value or "").strip()
        if not value:
            raise SettingsError("The API key is empty.", field="api_key",
                                hint="Paste the key again or remove the stored key.")
        if len(value) < 8:
            raise SettingsError("That API key looks too short.", field="api_key",
                                hint="Copy the whole key from your provider dashboard.")
        if len(value) > 500:
            raise SettingsError("That API key is too long.", field="api_key")
        secrets_map = self._load_secrets()
        secrets_map[name] = value
        self._save_secrets(secrets_map)
        log.info("Credential updated for %s.", name)
        return self.secret_status(name)

    def delete_secret(self, name: str) -> dict[str, Any]:
        if name not in SECRET_ENV_VARS:
            raise SettingsError("Unknown credential.", field="name")
        secrets_map = self._load_secrets()
        if name in secrets_map:
            secrets_map.pop(name)
            self._save_secrets(secrets_map)
            log.info("Credential removed for %s.", name)
        return self.secret_status(name)

    # -- session key -------------------------------------------------------- #
    def session_key(self) -> str:
        env_key = os.environ.get("FLASK_SECRET_KEY", "").strip()
        if env_key:
            return env_key
        try:
            if SESSION_KEY_PATH.is_file():
                value = SESSION_KEY_PATH.read_text(encoding="utf-8").strip()
                if len(value) >= 32:
                    return value
        except OSError:
            pass
        value = py_secrets.token_urlsafe(48)
        try:
            write_bytes_atomic(SESSION_KEY_PATH, value.encode("utf-8"), mode=0o600)
        except OSError as exc:  # pragma: no cover - read-only FS
            log.warning("Could not persist session key (%s); using an ephemeral key.", exc)
        return value


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return "••••••••" + value[-4:]


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read %s (%s); using defaults.", path.name, exc)
    return default


def _merge_defaults(defaults: dict, overrides: dict) -> dict:
    merged: dict[str, Any] = {}
    for key, value in defaults.items():
        if isinstance(value, dict):
            incoming = overrides.get(key, {})
            merged[key] = _merge_defaults(value, incoming if isinstance(incoming, dict) else {})
        else:
            merged[key] = overrides.get(key, value)
    return merged


def _validate_public_patch(patch: dict, current: dict) -> dict:
    """Whitelist + type-check every public setting. Unknown keys are rejected."""
    allowed_sections = {"llm", "tts", "video"}
    cleaned: dict[str, Any] = {}

    for section, values in patch.items():
        if section not in allowed_sections:
            raise SettingsError(f"Unknown settings section '{section}'.", field=section)
        if not isinstance(values, dict):
            raise SettingsError(f"Settings section '{section}' must be an object.", field=section)
        cleaned[section] = {}

        if section == "llm":
            if "provider" in values:
                provider = str(values["provider"])
                if provider not in LLM_PROVIDERS:
                    raise SettingsError("Unsupported script provider.", field="llm.provider")
                cleaned[section]["provider"] = provider
            if "model" in values:
                cleaned[section]["model"] = _clean_str(values["model"], 120, "llm.model")
            if "base_url" in values:
                base_url = _clean_str(values["base_url"], 200, "llm.base_url", allow_empty=True)
                if base_url and not base_url.startswith(("http://", "https://")):
                    raise SettingsError("Base URL must start with http:// or https://.",
                                        field="llm.base_url")
                cleaned[section]["base_url"] = base_url.rstrip("/")
            if "force_template" in values:
                cleaned[section]["force_template"] = bool(values["force_template"])

        if section == "tts":
            if "engine" in values:
                engine = str(values["engine"])
                if engine not in TTS_ENGINES:
                    raise SettingsError("Unsupported voice engine.", field="tts.engine")
                cleaned[section]["engine"] = engine
            if "fallback_engine" in values:
                fallback = str(values["fallback_engine"])
                if fallback and fallback != "none" and fallback not in TTS_ENGINES:
                    raise SettingsError("Unsupported fallback engine.", field="tts.fallback_engine")
                cleaned[section]["fallback_engine"] = fallback
            for key in ("voice", "fallback_voice"):
                if key in values:
                    cleaned[section][key] = _clean_str(values[key], 120, f"tts.{key}")
            if "speed" in values:
                try:
                    speed = float(values["speed"])
                except (TypeError, ValueError) as exc:
                    raise SettingsError("Speech speed must be a number.", field="tts.speed") from exc
                if not 0.5 <= speed <= 2.0:
                    raise SettingsError("Speech speed must be between 0.5 and 2.0.",
                                        field="tts.speed")
                cleaned[section]["speed"] = round(speed, 2)

        if section == "video":
            if "music_volume" in values:
                try:
                    volume = float(values["music_volume"])
                except (TypeError, ValueError) as exc:
                    raise SettingsError("Music volume must be a number.", field="video.music_volume") from exc
                if not 0.0 <= volume <= 1.0:
                    raise SettingsError("Music volume must be between 0 and 1.",
                                        field="video.music_volume")
                cleaned[section]["music_volume"] = round(volume, 2)
            if "tone" in values:
                tone = str(values["tone"])
                if tone not in ("warm", "premium", "concise"):
                    raise SettingsError("Unknown script tone.", field="video.tone")
                cleaned[section]["tone"] = tone

    return cleaned


def _clean_str(value: Any, limit: int, field: str, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if not text and not allow_empty:
        raise SettingsError("This field cannot be empty.", field=field)
    if len(text) > limit:
        raise SettingsError(f"This field must be {limit} characters or fewer.", field=field)
    if "\n" in text or "\r" in text:
        raise SettingsError("Line breaks are not allowed here.", field=field)
    return text


store = SettingsStore()
