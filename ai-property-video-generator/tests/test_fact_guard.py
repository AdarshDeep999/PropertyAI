"""The fact guard must reject fabricated numbers and claims."""

from __future__ import annotations

from services import fact_guard
from services.script_generator import word_budget
from services.template_script import generate_template_script


def _base_script(sample_facts, image_count=4):
    budget = word_budget(image_count)
    return generate_template_script(
        sample_facts, image_count,
        max_words_per_scene=budget["scene"],
        max_words_intro=budget["intro"],
        max_words_cta=budget["cta"],
    )


def test_template_output_is_grounded(sample_facts):
    assert fact_guard.check_script(_base_script(sample_facts), sample_facts) == []


def test_rejects_invented_price(sample_facts):
    script = _base_script(sample_facts)
    script["intro_narration"] = "Offered at $1,250,000 in Portside."
    problems = fact_guard.check_script(script, sample_facts)
    assert any("1,250,000" in problem for problem in problems)


def test_accepts_supplied_price_in_other_formats(sample_facts):
    script = _base_script(sample_facts)
    script["intro_narration"] = "Offered at 685000 dollars."
    assert fact_guard.check_script(script, sample_facts) == []


def test_rejects_unsupported_bedroom_count(sample_facts):
    script = _base_script(sample_facts)
    script["scenes"][0]["narration"] = "This home has 5 bedrooms."
    problems = fact_guard.check_script(script, sample_facts)
    assert any("5" in problem for problem in problems)


def test_accepts_supplied_bedroom_count(sample_facts):
    script = _base_script(sample_facts)
    script["scenes"][0]["narration"] = "This home has 3 bedrooms."
    assert fact_guard.check_script(script, sample_facts) == []


def test_rejects_placeholder_artifacts(sample_facts):
    script = _base_script(sample_facts)
    script["headline"] = "None"
    problems = fact_guard.check_script(script, sample_facts)
    assert any("none" in problem.lower() for problem in problems)


def test_rejects_unsupported_superlatives(sample_facts):
    script = _base_script(sample_facts)
    script["cta_headline"] = "The best home in Portside"
    problems = fact_guard.check_script(script, sample_facts)
    assert any("best" in problem for problem in problems)


def test_allows_words_the_user_actually_used(sample_facts):
    facts = sample_facts
    facts.description = "A perfect home with a luxury finish."
    script = _base_script(facts)
    script["cta_headline"] = "A perfect home"
    assert fact_guard.check_script(script, facts) == []


def test_rejects_price_when_none_supplied():
    from services.validation import PropertyFacts

    facts = PropertyFacts(name="Harbour Flat", location="Portside")
    script = _base_script(facts)
    script["intro_narration"] = "Yours for $450,000."
    problems = fact_guard.check_script(script, facts)
    assert any("no price was supplied" in problem for problem in problems)
