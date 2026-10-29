"""Provider catalogue: the single place that knows what the app can call.

Nothing here contains a credential. The catalogue is what the Settings UI is
built from, and the backend uses the same definitions when validating a request,
so a provider cannot be selected in the browser unless the server supports it.
"""

from __future__ import annotations

import functools
import os
import subprocess

from services.logging_setup import get_logger

log = get_logger("providers")

# --------------------------------------------------------------------------- #
# LLM script generation
# --------------------------------------------------------------------------- #
LLM_PROVIDERS: dict[str, dict] = {
    "gemini": {
        "label": "Google Gemini",
        "sdk": "REST (generateContent)",
        "requires_key": True,
        "secret": "gemini",
        "default_model": "gemini-3.5-flash",
        # Retired models (2.5 family) are rejected for new keys; newer 3.7/3.8
        # endpoints were not reliable with generateContent when tested.
        "models": ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite",
                   "gemini-3.1-flash-lite"],
        "docs_url": "https://aistudio.google.com/apikey",
        "notes": "Free tier available. Structured JSON requested with responseMimeType.",
    },
    "openai_compatible": {
        "label": "OpenAI-compatible",
        "sdk": "REST (chat/completions)",
        "requires_key": True,  # local Ollama is the exception, handled in the client
        "secret": "openai_compat",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "glm-4-flash", "llama3.1"],
        "docs_url": "https://platform.openai.com/api-keys",
        "notes": "Works with OpenAI, Z.ai, Groq, OpenRouter and local Ollama "
                 "(set the base URL to http://localhost:11434/v1 and leave the key blank).",
        "presets": [
            {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
            {"label": "Z.ai (GLM)", "base_url": "https://api.z.ai/api/paas/v4", "model": "glm-4-flash"},
            {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "model": "llama3.1"},
            {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini"},
            {"label": "Local Ollama (no key)", "base_url": "http://localhost:11434/v1", "model": "llama3.1"},
        ],
    },
}

# --------------------------------------------------------------------------- #
# Voice-over engines
# --------------------------------------------------------------------------- #
EDGE_VOICES = [
    {"id": "en-US-AriaNeural", "label": "Aria — en-US female, warm"},
    {"id": "en-US-GuyNeural", "label": "Guy — en-US male, natural"},
    {"id": "en-US-JennyNeural", "label": "Jenny — en-US female, friendly"},
    {"id": "en-US-ChristopherNeural", "label": "Christopher — en-US male, deep"},
    {"id": "en-GB-SoniaNeural", "label": "Sonia — en-GB female"},
    {"id": "en-GB-RyanNeural", "label": "Ryan — en-GB male"},
    {"id": "en-AU-NatashaNeural", "label": "Natasha — en-AU female"},
    {"id": "en-IN-NeerjaNeural", "label": "Neerja — en-IN female"},
]

OPENAI_TTS_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]

ELEVENLABS_VOICES = [
    {"id": "21m00Tcm4TlvDq8ikWAM", "label": "Rachel — calm, narration"},
    {"id": "EXAVITQu4vr4xnSDxMaL", "label": "Sarah — soft, professional"},
    {"id": "TxGEqnHWrfWFTfGW9XjX", "label": "Josh — deep, promotional"},
    {"id": "pNInz6obpgDQGcFmaJgB", "label": "Adam — clear, neutral"},
]

TTS_ENGINES: dict[str, dict] = {
    "edge": {
        "label": "Microsoft Edge neural voices",
        "requires_key": False,
        "needs_internet": True,
        "offline": False,
        "secret": None,
        "voices": EDGE_VOICES,
        "default_voice": "en-US-AriaNeural",
        "format": "mp3",
        "notes": "Free and keyless. Uses Microsoft's read-aloud endpoint; requires internet.",
    },
    "windows_sapi": {
        "label": "Windows built-in speech (offline)",
        "requires_key": False,
        "needs_internet": False,
        "offline": True,
        "secret": None,
        "voices": [],  # filled at runtime from the machine
        "default_voice": "Microsoft Zira Desktop",
        "format": "wav",
        "notes": "Works with no internet and no key. Voice quality is robotic.",
    },
    "openai": {
        "label": "OpenAI text-to-speech",
        "requires_key": True,
        "needs_internet": True,
        "offline": False,
        "secret": "openai_tts",
        "voices": [{"id": v, "label": v.title()} for v in OPENAI_TTS_VOICES],
        "default_voice": "alloy",
        "models": ["gpt-4o-mini-tts", "tts-1", "tts-1-hd"],
        "default_model": "gpt-4o-mini-tts",
        "format": "mp3",
        "notes": "Paid per character.",
    },
    "elevenlabs": {
        "label": "ElevenLabs",
        "requires_key": True,
        "needs_internet": True,
        "offline": False,
        "secret": "elevenlabs",
        "voices": ELEVENLABS_VOICES,
        "default_voice": "21m00Tcm4TlvDq8ikWAM",
        "format": "mp3",
        "notes": "Free tier has a monthly character allowance.",
    },
}

TTS_ENGINE_ORDER = ["edge", "windows_sapi", "openai", "elevenlabs"]


def llm_provider(provider_id: str) -> dict:
    return LLM_PROVIDERS.get(provider_id) or LLM_PROVIDERS["gemini"]


def tts_engine(engine_id: str) -> dict | None:
    return TTS_ENGINES.get(engine_id)


@functools.lru_cache(maxsize=1)
def sapi_voices() -> list[dict]:
    """Enumerate Windows SAPI voices. Falls back to the two default en-US voices."""
    fallback = [
        {"id": "Microsoft Zira Desktop", "label": "Microsoft Zira — en-US female (offline)"},
        {"id": "Microsoft David Desktop", "label": "Microsoft David — en-US male (offline)"},
    ]
    if os.name != "nt":
        return fallback
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | "
        "ForEach-Object { $_.VoiceInfo.Name + '|' + $_.VoiceInfo.Culture }"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        voices = []
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            name, culture = line.split("|", 1)
            name, culture = name.strip(), culture.strip()
            if name:
                voices.append({"id": name, "label": f"{name} — {culture or 'unknown'} (offline)"})
        return voices or fallback
    except Exception as exc:  # pragma: no cover - depends on host
        log.warning("Could not enumerate SAPI voices: %s", exc)
        return fallback


def public_catalog() -> dict:
    """Serialisable catalogue for the Settings UI. Contains no secrets."""
    engines = {}
    for engine_id in TTS_ENGINE_ORDER:
        engine = dict(TTS_ENGINES[engine_id])
        if engine_id == "windows_sapi":
            engine["voices"] = sapi_voices()
            engine["available"] = os.name == "nt"
        else:
            engine["available"] = True
        # `secret` is the *name* of the credential slot (e.g. "gemini"), not a
        # credential. The Settings UI needs it to know which key field to save.
        engines[engine_id] = engine

    providers = {}
    for provider_id, provider in LLM_PROVIDERS.items():
        providers[provider_id] = dict(provider)

    return {
        "llm": providers,
        "llm_order": ["gemini", "openai_compatible"],
        "tts": engines,
        "tts_order": TTS_ENGINE_ORDER,
        "tones": ["warm", "premium", "concise"],
    }
