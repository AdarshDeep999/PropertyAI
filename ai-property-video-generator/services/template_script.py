"""Deterministic template script generator.

This is the guaranteed fallback: no network, no key, no randomness. Given the
same property and photo count it returns byte-identical output, which makes it
testable and predictable in a demo.

It only ever states facts the user supplied. Where a photo has no matching fact
it uses neutral viewing language ("A closer look") rather than inventing a room
name, a feature or an adjective like "spacious".
"""

from __future__ import annotations

from typing import Any

from services.validation import PropertyFacts

MAX_CAPTION_WORDS = 9

_NEUTRAL_SCENES = [
    ("A closer look", "A closer look at {name}."),
    ("Inside the property", "Step inside {name}."),
    ("Part of the layout", "Another part of the property."),
    ("More to see", "Continue through {name}."),
    ("Another view", "Another view in {location}."),
    ("Details", "A closer view of the details."),
    ("Room to explore", "There is more to see."),
    ("Final look", "One last look at {name}."),
]


def _words(text: str) -> int:
    return len([w for w in text.split() if w.strip()])


def _fit(sentences: list[str], max_words: int) -> str:
    """Keep whole sentences up to the word budget, never cut mid-sentence.

    The budget is a target, not a hard cap: if not even the first sentence fits
    it is kept whole and the timeline extends the scene instead. A slightly
    longer scene is far better than narration that stops mid-sentence.
    """
    chosen: list[str] = []
    for sentence in sentences:
        candidate = " ".join(chosen + [sentence])
        if _words(candidate) <= max_words:
            chosen.append(sentence)
    if chosen:
        return " ".join(chosen)
    return sentences[0] if sentences else ""


def _spoken_contact(contact: str) -> str:
    """A short, speakable version of the contact line.

    Email addresses and phone numbers are read aloud very slowly (and an email
    becomes a string of letters), so narration deliberately omits anything with
    digits or an @ sign. The full contact line is still shown on screen.
    """
    first = (contact or "").split("·")[0].split("|")[0].strip(" ,;-–")
    if not first or len(first) > 42 or any(char.isdigit() for char in first) or "@" in first:
        return ""
    return first


def _shorten(text: str, max_words: int = MAX_CAPTION_WORDS) -> str:
    words = [w for w in (text or "").split() if w.strip()]
    if not words:
        return ""
    clipped = " ".join(words[:max_words]).rstrip(",.;:")
    return clipped


def _sentence(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if text[-1] not in ".!?":
        text += "."
    return text[0].upper() + text[1:] if text else text


def _description_sentences(description: str) -> list[str]:
    import re

    if not description:
        return []
    parts = re.split(r"(?<=[.!?])\s+", description.replace("\n", " "))
    return [p.strip() for p in parts if len(p.strip()) > 12]


def generate_template_script(facts: PropertyFacts, image_count: int, *,
                             max_words_per_scene: int = 26,
                             max_words_intro: int = 30,
                             max_words_cta: int = 24,
                             tone: str = "warm") -> dict[str, Any]:
    """Build a complete, grounded script for ``image_count`` photo scenes."""
    name, location = facts.name, facts.location
    count = max(1, int(image_count))

    # --- opening ---------------------------------------------------------- #
    intro_sentences = [f"{name} in {location}."]
    if facts.property_type and facts.bedrooms is not None:
        intro_sentences.append(f"This {facts.property_type.lower()} has {facts.bedrooms} bedrooms.")
    elif facts.property_type:
        intro_sentences.append(f"It is a {facts.property_type.lower()}.")
    elif facts.bedrooms is not None:
        intro_sentences.append(f"It has {facts.bedrooms} bedrooms.")
    if facts.price:
        intro_sentences.append(f"The asking price is {facts.price}.")

    intro_narration = _fit(intro_sentences, max_words_intro)
    headline = name if len(name) <= 42 else _shorten(name, 8)

    # --- photo scenes ----------------------------------------------------- #
    features = list(facts.features)
    description_sentences = _description_sentences(facts.description)
    scenes: list[dict[str, str]] = []

    for index in range(count):
        caption = ""
        narration = ""

        if index < len(features):
            feature = features[index].strip().rstrip(".")
            caption = _shorten(feature)
            lower = feature[0].lower() + feature[1:] if len(feature) > 1 else feature.lower()
            variants = [
                f"{_sentence(feature)}",
                f"The property includes {lower}.",
                f"Also featured: {lower}.",
                f"You'll find {lower}.",
            ]
            narration = _fit([variants[index % len(variants)]], max_words_per_scene)
        elif index < len(description_sentences) and \
                _words(description_sentences[index]) <= max_words_per_scene:
            sentence = description_sentences[index]
            caption = _shorten(sentence)
            narration = _fit([_sentence(sentence)], max_words_per_scene)
        else:
            neutral_caption, neutral_line = _NEUTRAL_SCENES[index % len(_NEUTRAL_SCENES)]
            caption = neutral_caption
            narration = _fit(
                [neutral_line.format(name=name, location=location)], max_words_per_scene
            )

        scenes.append({"caption": caption or "A closer look", "narration": narration})

    # --- closing ---------------------------------------------------------- #
    cta_line = facts.contact or "Enquire today to arrange a viewing."
    spoken_contact = _spoken_contact(facts.contact)
    if spoken_contact:
        cta_narration = _fit([f"To arrange a viewing, contact {spoken_contact}."],
                             max_words_cta)
    else:
        cta_narration = _fit(["To arrange a viewing, get in touch today."], max_words_cta)

    cta_headline = "Arrange a viewing"

    return {
        "headline": headline,
        "kicker": "Property showcase",
        "intro_narration": intro_narration,
        "scenes": scenes,
        "cta_headline": cta_headline,
        "cta_line": cta_line,
        "cta_narration": cta_narration,
        "tone": tone,
    }
