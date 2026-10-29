"""Fact guard: refuse generated copy that contains unsupported claims.

This is the safety net behind "use only the property facts supplied by the user".
It is deliberately strict and cheap:

* every number in the generated text must appear somewhere in the user's own
  input (allowing 450,000 / 450000 / 450k / 450K to match),
* currency symbols must be backed by a supplied price,
* placeholder artifacts (None, null, nan, {}, []) are rejected,
* a short list of unsupported superlatives is rejected unless the user used the
  word themselves.

Strictness is intentional: a false rejection costs one template fallback, while
a false acceptance would put a fabricated price in a client's marketing video.
"""

from __future__ import annotations

import re

from services.validation import PropertyFacts

_ARTIFACTS = ("none", "null", "undefined", "nan", "{}", "[]", "{{", "}}", "todo", "lorem ipsum")
_CURRENCY_RE = re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d+)?\s?[kKmM]?")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s?[kKmM]?")
_SUPERLATIVES = (
    "best", "#1", "number one", "guaranteed", "unbeatable", "cheapest",
    "most affordable", "luxury", "luxurious", "exclusive", "prestigious",
    "perfect", "dream home", "must-see", "once in a lifetime", "world class",
    "unparalleled", "state of the art", "newly renovated", "fully renovated",
)


def _digit_forms(token: str) -> set[str]:
    """Return acceptable normalised forms for a numeric token."""
    raw = token.strip().lower().replace(",", "").replace(" ", "")
    forms: set[str] = set()
    multiplier = 1
    if raw.endswith("k"):
        multiplier, raw = 1000, raw[:-1]
    elif raw.endswith("m"):
        multiplier, raw = 1_000_000, raw[:-1]
    try:
        value = float(raw)
    except ValueError:
        return forms
    if multiplier > 1:
        scaled = value * multiplier
        forms.add(str(int(scaled)))
        forms.add(f"{scaled:.0f}")
    forms.add(str(int(value)) if value.is_integer() else f"{value:g}")
    return forms


def allowed_numbers(facts: PropertyFacts) -> set[str]:
    numbers: set[str] = set()
    for token in _NUMBER_RE.findall(facts.all_user_text()):
        numbers |= _digit_forms(token)
    if facts.bedrooms is not None:
        numbers.add(str(facts.bedrooms))
    return numbers


def check_text(text: str, facts: PropertyFacts, allowed: set[str] | None = None) -> list[str]:
    """Return a list of problems; empty means the text is grounded."""
    problems: list[str] = []
    lowered = (text or "").lower()
    if not lowered.strip():
        return ["empty text"]

    for artifact in _ARTIFACTS:
        if artifact in ("{}", "[]"):
            if artifact in lowered:
                problems.append(f"placeholder artifact '{artifact}'")
        elif re.search(rf"\b{re.escape(artifact)}\b", lowered):
            problems.append(f"placeholder artifact '{artifact}'")

    pool = allowed if allowed is not None else allowed_numbers(facts)
    for token in _NUMBER_RE.findall(text or ""):
        forms = _digit_forms(token)
        if forms and not (forms & pool):
            problems.append(f"unsupported number '{token.strip()}'")

    for token in _CURRENCY_RE.findall(text or ""):
        if not facts.price:
            problems.append(f"price mentioned but no price was supplied ('{token.strip()}')")

    facts_lower = facts.all_user_text().lower()
    for phrase in _SUPERLATIVES:
        if phrase in lowered and phrase not in facts_lower:
            problems.append(f"unsupported claim '{phrase}'")

    return _dedupe(problems)


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def check_script(data: dict, facts: PropertyFacts) -> list[str]:
    """Check every user-visible string in a structured script."""
    allowed = allowed_numbers(facts)
    texts: list[str] = []
    for key in ("headline", "kicker", "intro_narration", "cta_headline", "cta_line",
                "cta_narration"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            texts.append(value)
    for scene in data.get("scenes", []) or []:
        if isinstance(scene, dict):
            texts.extend(str(scene.get(k, "")) for k in ("caption", "narration"))

    problems: list[str] = []
    for text in texts:
        for problem in check_text(text, facts, allowed):
            problems.append(f"{problem} in: \"{text[:70]}\"")
    return _dedupe(problems)
