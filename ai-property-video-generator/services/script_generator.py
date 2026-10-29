"""AI script generation with a deterministic fallback.

Flow for every request:

1. Load provider/model/key from the server-side settings store.
2. Ask the model for a strict JSON shape, with per-scene word budgets derived
   from the photo count (so the finished video lands in 20–40 seconds).
3. Extract JSON defensively (models sometimes wrap it in prose or fences).
4. Validate the schema, word budgets and — critically — the *facts*.
5. Retry at most twice, and only for failures that can succeed on a retry
   (timeouts, 429, 5xx, malformed output). Never retry an invalid key, a quota
   error or an unknown model.
6. On any failure, return a template script with a human-readable reason. The
   caller always receives a usable script tagged with its real source.
"""

from __future__ import annotations

import dataclasses
import json
import random
import re
import time
from typing import Any, Callable

import requests

from config.providers import LLM_PROVIDERS
from config.settings import SettingsStore, store as default_store
from services import fact_guard
from services.errors import (
    AppError,
    MalformedAIResponse,
    ProviderError,
    classify_request_exception,
    error_from_http,
)
from services.logging_setup import get_logger
from services.template_script import generate_template_script
from services.validation import PropertyFacts

log = get_logger("script")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
BANNED_CLAIMS = (
    "best, #1, guaranteed, unbeatable, cheapest, most affordable, luxury, exclusive, "
    "prestigious, perfect, dream home, must-see, once in a lifetime, world class, "
    "unparalleled, state of the art, newly renovated"
)


# --------------------------------------------------------------------------- #
# Word budgets
# --------------------------------------------------------------------------- #
def word_budget(image_count: int) -> dict[str, int]:
    """Narration word limits that keep the finished video at ~20–40 seconds.

    Measured on this machine: the Edge neural voices speak roughly 105–115
    words per minute including punctuation pauses, noticeably slower than the
    usual 160 wpm rule of thumb. These budgets were derived from rendered
    sample timings so the finished video lands in the 20–40 second window.
    """
    per_scene = {4: 11, 5: 10, 6: 8, 7: 7}.get(int(image_count), 7)
    intro = 10
    cta = 6
    total = intro + per_scene * int(image_count) + cta
    return {"intro": intro, "scene": per_scene, "cta": cta, "total": total}


def _scale_budget(budget: dict[str, int], word_scale: float) -> dict[str, int]:
    if word_scale <= 0 or abs(word_scale - 1.0) < 1e-6:
        return budget
    return {
        "intro": max(6, round(budget["intro"] * word_scale)),
        "scene": max(6, round(budget["scene"] * word_scale)),
        "cta": max(6, round(budget["cta"] * word_scale)),
        "total": max(20, round(budget["total"] * word_scale)),
    }


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class ScriptResult:
    data: dict[str, Any]
    source: str = "template"
    provider: str | None = None
    model: str | None = None
    attempts: int = 0
    fallback_reason: str | None = None
    warnings: list[str] = dataclasses.field(default_factory=list)
    technical_detail: str | None = None

    @property
    def is_ai(self) -> bool:
        return self.source == "ai"

    def to_public(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "source": self.source,
            "provider": self.provider,
            "model": self.model,
            "attempts": self.attempts,
            "fallback_reason": self.fallback_reason,
            "warnings": list(self.warnings),
            "is_ai": self.is_ai,
        }


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
def _system_prompt() -> str:
    return (
        "You are a careful real-estate copywriter producing a short promotional video script.\n"
        "HARD RULES:\n"
        "1. Use ONLY facts from the FACTS JSON. Never invent a price, address, dimension, "
        "amenity, room name, phone number, email, school, or transport link.\n"
        "2. If a fact is absent, do not mention it. Never write 'null', 'None' or placeholders.\n"
        f"3. Avoid unsupported superlatives and claims: {BANNED_CLAIMS}.\n"
        "4. Do not repeat the same sentence pattern or adjective in every scene.\n"
        "5. Captions are on-screen text: 9 words maximum, no trailing full stop required.\n"
        "6. Narration is spoken text: one or two short sentences, within the word limit given.\n"
        "7. The number of scene objects MUST equal the requested photo count.\n"
        "Reply with JSON only. No markdown, no commentary."
    )


def _user_prompt(facts: PropertyFacts, image_count: int, budget: dict[str, int], tone: str) -> str:
    shape = {
        "headline": "short title for the opening scene (max 8 words)",
        "kicker": "2-3 word label, e.g. Property showcase",
        "intro_narration": f"spoken opening, max {budget['intro']} words",
        "scenes": [
            {
                "caption": "on-screen text, max 9 words",
                "narration": f"spoken line, max {budget['scene']} words",
            }
        ],
        "cta_headline": "short closing call to action (max 5 words)",
        "cta_line": "one line shown on the final screen",
        "cta_narration": f"spoken closing, max {budget['cta']} words",
    }
    return (
        f"FACTS (the only permitted source of truth):\n{json.dumps(facts.clean_dict(), ensure_ascii=False, indent=2)}\n\n"
        f"PHOTO COUNT: {image_count}\n"
        f"TONE: {tone}\n"
        f"WORD LIMITS: intro <= {budget['intro']}, each scene <= {budget['scene']}, "
        f"cta <= {budget['cta']}.\n\n"
        f"Return exactly {image_count} scene objects, in viewing order, matching this shape:\n"
        f"{json.dumps(shape, indent=2, ensure_ascii=False)}"
    )


# --------------------------------------------------------------------------- #
# JSON extraction and validation
# --------------------------------------------------------------------------- #
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


def extract_json(text: str) -> dict:
    """Pull the first balanced JSON object out of a model response."""
    if not text or not text.strip():
        raise MalformedAIResponse("empty model response")

    candidate = text.strip()
    fence = _FENCE_RE.search(candidate)
    if fence:
        candidate = fence.group(1).strip()

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    while start != -1:
        depth, in_string, escape = 0, False, False
        for index in range(start, len(candidate)):
            char = candidate[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    snippet = candidate[start:index + 1]
                    try:
                        parsed = json.loads(snippet)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        break
        start = candidate.find("{", start + 1)

    raise MalformedAIResponse(f"could not parse JSON from {len(text)} chars of model output")


def _word_count(text: str) -> int:
    return len([w for w in text.split() if w.strip()])


def validate_script(data: dict, image_count: int, budget: dict[str, int]) -> dict:
    """Schema + length validation. Raises MalformedAIResponse on any problem."""
    if not isinstance(data, dict):
        raise MalformedAIResponse("script payload is not an object")

    required_text = ["headline", "intro_narration", "cta_headline", "cta_line", "cta_narration"]
    clean: dict[str, Any] = {}
    for key in required_text:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise MalformedAIResponse(f"missing or empty field '{key}'")
        clean[key] = " ".join(value.split())

    # A small tolerance keeps a natural sentence from being rejected for being a
    # word over budget. The template generator behaves the same way: it never
    # truncates speech, it lets the timeline extend the scene instead.
    tolerance = 3
    if _word_count(clean["intro_narration"]) > budget["intro"] + tolerance:
        raise MalformedAIResponse(
            f"intro narration too long ({_word_count(clean['intro_narration'])} > "
            f"{budget['intro'] + tolerance})")
    if _word_count(clean["cta_narration"]) > budget["cta"] + tolerance:
        raise MalformedAIResponse(
            f"cta narration too long ({_word_count(clean['cta_narration'])} > "
            f"{budget['cta'] + tolerance})")

    raw_scenes = data.get("scenes")
    if not isinstance(raw_scenes, list):
        raise MalformedAIResponse("'scenes' is not a list")
    if len(raw_scenes) != int(image_count):
        raise MalformedAIResponse(
            f"expected {image_count} scenes, got {len(raw_scenes)}")

    scenes: list[dict[str, str]] = []
    for index, scene in enumerate(raw_scenes):
        if not isinstance(scene, dict):
            raise MalformedAIResponse(f"scene {index + 1} is not an object")
        caption = scene.get("caption")
        narration = scene.get("narration")
        if not isinstance(caption, str) or not _caption_ok(caption):
            raise MalformedAIResponse(f"scene {index + 1} caption missing")
        if not isinstance(narration, str) or not narration.strip():
            raise MalformedAIResponse(f"scene {index + 1} narration missing")
        caption_clean = " ".join(caption.split())
        narration_clean = " ".join(narration.split())
        if _word_count(caption_clean) > 12:
            # Captions are read, not spoken, so a safe truncation is acceptable.
            caption_clean = " ".join(caption_clean.split()[:9])
        if _word_count(narration_clean) > budget["scene"] + tolerance:
            raise MalformedAIResponse(
                f"scene {index + 1} narration too long "
                f"({_word_count(narration_clean)} > {budget['scene'] + tolerance})")
        scenes.append({"caption": caption_clean, "narration": narration_clean})

    clean["scenes"] = scenes
    clean["kicker"] = " ".join(str(data.get("kicker") or "Property showcase").split())[:40]
    clean["tone"] = " ".join(str(data.get("tone") or "").split())[:30]
    return clean


def _caption_ok(caption: str) -> bool:
    return bool(caption and caption.strip())


# --------------------------------------------------------------------------- #
# HTTP adapters
# --------------------------------------------------------------------------- #
def _post_json(url: str, *, headers: dict, payload: dict, timeout: int,
               provider: str) -> dict:
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except Exception as exc:
        raise classify_request_exception(provider, exc) from exc
    if response.status_code >= 400:
        raise error_from_http(provider, response.status_code, response.text[:800])
    try:
        return response.json()
    except ValueError as exc:
        raise MalformedAIResponse(f"{provider} returned non-JSON HTTP body") from exc


def _call_gemini(api_key: str, model: str, system: str, user: str, timeout: int) -> str:
    model = (model or "").strip()
    if model.startswith("models/"):
        model = model[len("models/"):]
    url = f"{GEMINI_BASE}/models/{model}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": 0.75,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json",
            # Gemini 3.x spends output tokens on internal reasoning by default;
            # a short marketing script does not need it, and leaving it on caused
            # MAX_TOKENS truncation before any JSON was returned.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    data = _post_json(url, headers={"x-goog-api-key": api_key,
                                    "Content-Type": "application/json"},
                      payload=payload, timeout=timeout, provider="Gemini")
    candidates = data.get("candidates") or []
    if not candidates:
        feedback = (data.get("promptFeedback") or {}).get("blockReason")
        raise MalformedAIResponse(f"Gemini returned no candidates (blockReason={feedback})")
    candidate = candidates[0]
    finish = candidate.get("finishReason")
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(str(part.get("text", "")) for part in parts
                   if isinstance(part, dict) and not part.get("thought"))
    if not text.strip():
        raise MalformedAIResponse(f"Gemini returned empty content (finishReason={finish})")
    if finish == "MAX_TOKENS":
        raise MalformedAIResponse("Gemini response was truncated (MAX_TOKENS)")
    return text


def _call_openai_compatible(api_key: str, base_url: str, model: str, system: str,
                            user: str, timeout: int) -> str:
    base = (base_url or "https://api.openai.com/v1").rstrip("/")
    url = f"{base}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.75,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except Exception as exc:
        raise classify_request_exception("The script provider", exc) from exc

    if response.status_code == 400 and "response_format" in (response.text or "").lower():
        # Some OpenAI-compatible servers do not implement response_format.
        payload.pop("response_format", None)
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except Exception as exc:
            raise classify_request_exception("The script provider", exc) from exc

    if response.status_code >= 400:
        raise error_from_http("The script provider", response.status_code, response.text[:800])

    try:
        data = response.json()
    except ValueError as exc:
        raise MalformedAIResponse("script provider returned non-JSON HTTP body") from exc
    choices = data.get("choices") or []
    if not choices:
        raise MalformedAIResponse("script provider returned no choices")
    message = choices[0].get("message") or {}
    text = message.get("content")
    if isinstance(text, list):  # some providers return content parts
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    if not isinstance(text, str) or not text.strip():
        raise MalformedAIResponse("script provider returned empty content")
    return text


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def generate_script(
    facts: PropertyFacts,
    image_count: int,
    *,
    settings_store: SettingsStore | None = None,
    sleep: Callable[[float], None] | None = None,
    client: Callable[..., str] | None = None,
    word_scale: float = 1.0,
) -> ScriptResult:
    """Generate a script, falling back to the deterministic template on any failure.

    ``client`` and ``sleep`` exist so tests can exercise retry/fallback paths
    without network calls or real delays.
    """
    store = settings_store or default_store
    config = store.public()
    llm_cfg = config.get("llm", {})
    budget = _scale_budget(word_budget(image_count), word_scale)
    tone = config.get("video", {}).get("tone", "warm")

    def template(reason: str, *, attempts: int = 0, technical: str | None = None,
                 warnings: list[str] | None = None) -> ScriptResult:
        data = generate_template_script(
            facts, image_count,
            max_words_per_scene=budget["scene"],
            max_words_intro=budget["intro"],
            max_words_cta=budget["cta"],
            tone=tone,
        )
        result = ScriptResult(
            data=data, source="template", provider=llm_cfg.get("provider"),
            model=llm_cfg.get("model"), attempts=attempts, fallback_reason=reason,
            warnings=warnings or [], technical_detail=technical,
        )
        log.warning("Using template script (%s).", reason)
        if technical:
            log.warning("Technical detail: %s", technical[:600])
        return result

    if llm_cfg.get("force_template"):
        return template("Template mode is enabled in Settings, so the built-in script was used.")

    provider_id = llm_cfg.get("provider") or "gemini"
    provider_meta = LLM_PROVIDERS.get(provider_id)
    if not provider_meta:
        return template(f"Unknown script provider '{provider_id}', so the template was used.")

    api_key = store.get_secret(provider_meta["secret"]) or ""
    base_url = (llm_cfg.get("base_url") or "").strip()
    is_local = base_url.startswith(("http://localhost", "http://127.0.0.1"))
    if provider_meta["requires_key"] and not api_key and not is_local:
        return template(
            f"No {provider_meta['label']} API key is configured, so the built-in template "
            "script was used. Add a key in Settings to use AI scripts."
        )

    model = (llm_cfg.get("model") or provider_meta["default_model"]).strip()
    system, user = _system_prompt(), _user_prompt(facts, image_count, budget, tone)
    max_attempts = 2
    delay = 1.2

    last_error: AppError | None = None
    attempts = 0
    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        try:
            if client is not None:
                text = client(provider_id, model, api_key, base_url, system, user)
            elif provider_id == "gemini":
                text = _call_gemini(api_key, model, system, user, timeout=25)
            else:
                text = _call_openai_compatible(api_key, base_url, model, system, user, timeout=25)

            parsed = extract_json(text)
            validated = validate_script(parsed, image_count, budget)
            problems = fact_guard.check_script(validated, facts)
            if problems:
                raise MalformedAIResponse("fact guard rejected copy: " + "; ".join(problems[:4]))

            result = ScriptResult(
                data=validated, source="ai", provider=provider_id, model=model,
                attempts=attempts, fallback_reason=None,
            )
            log.info("AI script generated with %s/%s in %d attempt(s).",
                     provider_id, model, attempts)
            return result

        except AppError as exc:
            last_error = exc
            log.warning("Script attempt %d/%d failed: %s", attempt, max_attempts,
                        exc.log_detail or exc.message)
            if not exc.retryable or attempt >= max_attempts:
                break
            if sleep is not None:
                sleep(delay + random.uniform(0, 0.2))
            else:
                time.sleep(delay + random.uniform(0, 0.2))
        except Exception as exc:  # unexpected bug in a provider adapter
            last_error = ProviderError(code="provider_error",
                                       message="The AI provider could not be used.",
                                       log_detail=repr(exc))
            log.exception("Unexpected script generation error")
            break

    reason = _fallback_reason(provider_meta["label"], last_error)
    return template(reason, attempts=attempts,
                    technical=last_error.log_detail if last_error else None)


def _fallback_reason(label: str, error: AppError | None) -> str:
    code = error.code if error else "error"
    mapping = {
        "invalid_credentials": f"{label} rejected the API key, so the built-in template script "
                               "was used. Update the key in Settings to use AI scripts.",
        "provider_permission": f"The {label} key does not have permission, so the built-in "
                               "template script was used.",
        "rate_limited": f"{label} is rate limiting requests right now, so the built-in template "
                        "script was used. Try again in a minute for an AI script.",
        "quota_exhausted": f"The {label} quota or billing limit was reached, so the built-in "
                           "template script was used.",
        "network_error": f"Could not reach {label}, so the built-in template script was used.",
        "unsupported_choice": f"{label} does not recognise that model, so the built-in template "
                              "script was used.",
        "malformed_ai_response": "The AI response could not be validated (it may have included "
                                 "details that were not supplied), so the built-in template "
                                 "script was used.",
    }
    return mapping.get(code, f"{label} could not be used, so the built-in template script was used.")


# --------------------------------------------------------------------------- #
# Connection test (real request, never a fake success)
# --------------------------------------------------------------------------- #
def test_llm_connection(settings_store: SettingsStore | None = None, *,
                        provider_id: str | None = None, model: str | None = None,
                        base_url: str | None = None, api_key: str | None = None) -> dict:
    store = settings_store or default_store
    config = store.public()["llm"]
    provider_id = provider_id or config["provider"]
    meta = LLM_PROVIDERS.get(provider_id)
    if not meta:
        return {"ok": False, "code": "unsupported_choice",
                "message": f"Unknown provider '{provider_id}'."}

    key = (api_key or store.get_secret(meta["secret"]) or "").strip()
    base = (base_url if base_url is not None else config.get("base_url") or "").strip()
    model_name = (model or config.get("model") or meta["default_model"]).strip()
    timeout = 20

    if meta["requires_key"] and not key and not base.startswith(("http://localhost", "http://127.0.0.1")):
        return {"ok": False, "code": "invalid_credentials",
                "message": f"No {meta['label']} key is saved yet. Paste a key, save it, then test."}

    try:
        if provider_id == "gemini":
            clean_model = model_name[len("models/"):] if model_name.startswith("models/") else model_name
            # A models lookup can list a model that this key may no longer use for
            # generation, so the test performs a tiny real generateContent call.
            response = requests.post(
                f"{GEMINI_BASE}/models/{clean_model}:generateContent",
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json={
                    "contents": [{"role": "user",
                                  "parts": [{"text": "Reply with the single word OK."}]}],
                    "generationConfig": {"maxOutputTokens": 32, "temperature": 0},
                },
                timeout=timeout,
            )
            if response.status_code != 200:
                raise error_from_http("Gemini", response.status_code, response.text[:400])
            candidates = (response.json() or {}).get("candidates") or []
            if not candidates:
                return {"ok": False, "code": "provider_error",
                        "message": "Gemini accepted the key but returned no answer for the test prompt.",
                        "hint": "Try the built-in template script, or choose another model."}
            return {"ok": True, "code": "connected",
                    "message": f"Connected. Gemini answered a test generation with '{clean_model}'."}

        base = (base or "https://api.openai.com/v1").rstrip("/")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        response = requests.get(f"{base}/models", headers=headers, timeout=timeout)
        if response.status_code != 200:
            raise error_from_http("The script provider", response.status_code, response.text[:400])
        available = []
        try:
            payload = response.json()
            available = [item.get("id", "") for item in payload.get("data", []) if isinstance(item, dict)]
        except ValueError:
            pass
        if available and model_name not in available and not model_name.startswith("openai/"):
            return {"ok": True, "code": "connected_model_unlisted",
                    "message": f"Key works. '{model_name}' was not in the returned model list — "
                               "generation will confirm whether the server accepts it."}
        return {"ok": True, "code": "connected",
                "message": f"Connected. Model '{model_name}' is available."}
    except AppError as exc:
        return {"ok": False, "code": exc.code, "message": exc.message,
                "hint": exc.hint, "retryable": exc.retryable}
    except Exception as exc:
        friendly = classify_request_exception("The script provider", exc)
        return {"ok": False, "code": friendly.code, "message": friendly.message,
                "hint": friendly.hint, "retryable": friendly.retryable}
