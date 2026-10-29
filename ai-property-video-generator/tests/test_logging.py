"""Log redaction: registered secrets and provider headers never appear."""

from __future__ import annotations

import io
import logging

from services.logging_setup import RedactionFilter, clear_registered_secrets, redact, register_secret

FAKE_KEY = "AIzaSyFAKEKEY1234567890"


def setup_function():
    clear_registered_secrets()


def test_registered_secret_is_redacted():
    register_secret(FAKE_KEY)
    assert FAKE_KEY not in redact(f"using key {FAKE_KEY} for Gemini")
    assert "[REDACTED]" in redact(f"using key {FAKE_KEY}")


def test_bearer_header_is_redacted():
    text = redact("Authorization: Bearer sk-abcdef1234567890abcdef")
    assert "sk-abcdef1234567890abcdef" not in text
    assert "Bearer [REDACTED]" in text


def test_google_key_header_is_redacted():
    text = redact("x-goog-api-key: AIzaSyAnotherKey123")
    assert "AIzaSyAnotherKey123" not in text


def test_query_key_is_redacted():
    text = redact("GET https://example.test/v1?key=supersecretvalue&x=1")
    assert "supersecretvalue" not in text


def test_api_key_assignment_is_redacted():
    text = redact('{"api_key": "abcdef1234567890"}')
    assert "abcdef1234567890" not in text


def test_filter_cleans_log_records():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(RedactionFilter())
    logger = logging.getLogger("apvg.test.redaction")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    register_secret(FAKE_KEY)
    logger.info("calling provider with %s", FAKE_KEY)
    logger.warning("Authorization: Bearer abcdef1234567890xyz")

    output = stream.getvalue()
    assert FAKE_KEY not in output
    assert "abcdef1234567890xyz" not in output
    assert "[REDACTED]" in output


def test_exception_text_is_cleaned():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionFilter())
    logger = logging.getLogger("apvg.test.exc")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    register_secret(FAKE_KEY)
    try:
        raise RuntimeError(f"failed with {FAKE_KEY}")
    except RuntimeError:
        logger.exception("provider exploded")

    assert FAKE_KEY not in stream.getvalue()
