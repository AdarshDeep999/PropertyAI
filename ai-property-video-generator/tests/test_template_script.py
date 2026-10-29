"""The deterministic template generator is the guaranteed fallback."""

from __future__ import annotations

from services import fact_guard
from services.script_generator import word_budget
from services.template_script import generate_template_script
from services.validation import PropertyFacts


def _script(facts, count=4, **kwargs):
    budget = word_budget(count)
    return generate_template_script(
        facts, count,
        max_words_per_scene=budget["scene"],
        max_words_intro=budget["intro"],
        max_words_cta=budget["cta"],
        **kwargs,
    )


def test_is_deterministic(sample_facts):
    first = _script(sample_facts)
    second = _script(sample_facts)
    assert first == second


def test_scene_count_matches_photo_count(sample_facts):
    for count in (4, 5, 6, 7, 8):
        assert len(_script(sample_facts, count)["scenes"]) == count


def test_captions_are_short(sample_facts):
    script = _script(sample_facts, 6)
    for scene in script["scenes"]:
        assert 0 < len(scene["caption"].split()) <= 9


def test_no_programming_artifacts(sample_facts):
    import re

    script = _script(sample_facts, 6)
    texts = [script["headline"], script["intro_narration"], script["cta_headline"],
             script["cta_line"], script["cta_narration"]]
    texts += [scene[field] for scene in script["scenes"] for field in ("caption", "narration")]
    blob = " ".join(texts).lower()
    for artifact in ("null", "undefined", "{", "}", "nan"):
        assert artifact not in blob
    assert not re.search(r"\bnone\b", blob)


def test_only_supplied_facts_are_used(sample_facts):
    script = _script(sample_facts, 6)
    assert fact_guard.check_script(script, sample_facts) == []


def test_contact_details_are_not_spoken(sample_facts):
    script = _script(sample_facts)
    spoken = script["cta_narration"].lower()
    assert "@" not in spoken
    assert "555" not in spoken
    # The on-screen line keeps the full details.
    assert "hello@coastalandco.example" in script["cta_line"]


def test_scene_narration_varies(sample_facts):
    script = _script(sample_facts, 5)
    narrations = [scene["narration"] for scene in script["scenes"]]
    assert len(set(narrations)) > 1


def test_works_with_minimal_facts():
    facts = PropertyFacts(name="Harbour Flat", location="Portside")
    script = _script(facts, 4)
    assert len(script["scenes"]) == 4
    assert script["cta_line"]  # generic enquiry line, not fabricated contact details
    assert "None" not in str(script)
    assert fact_guard.check_script(script, facts) == []


def test_price_is_mentioned_when_the_intro_has_room(sample_facts):
    # The default 10-word intro is spent on name + location, so the price is a
    # title-card element. With room, it is spoken as well.
    script = generate_template_script(sample_facts, 4, max_words_per_scene=8,
                                      max_words_intro=20, max_words_cta=8)
    assert "$685,000" in script["intro_narration"]


def test_price_is_absent_when_not_supplied():
    facts = PropertyFacts(name="Harbour Flat", location="Portside")
    script = _script(facts)
    assert "$" not in script["intro_narration"]


def test_long_description_does_not_produce_runaway_scenes():
    facts = PropertyFacts(
        name="Harbour Flat", location="Portside",
        description=("A very long paragraph " * 20).strip() + ".",
    )
    script = _script(facts, 4)
    for scene in script["scenes"]:
        assert len(scene["narration"].split()) <= 20


def test_spoken_contact_uses_short_agency_name():
    facts = PropertyFacts(
        name="Harbour Flat", location="Portside",
        contact="Coastal & Co. Realty · +1 555 0142 · hello@example.test",
    )
    script = _script(facts)
    assert "Coastal & Co. Realty" in script["cta_narration"]
    assert "example.test" not in script["cta_narration"]
