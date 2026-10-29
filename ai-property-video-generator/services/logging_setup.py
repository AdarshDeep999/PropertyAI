"""Logging with secret redaction.

Two defences keep credentials out of logs:

1. Every secret value the app knows about is registered with the redaction
   filter, so an accidental ``logger.info(key)`` is scrubbed.
2. Pattern-based scrubbing catches ``Authorization: Bearer ...``,
   ``x-goog-api-key: ...``, ``api_key=...`` and ``?key=...`` even for values the
   app does not hold (for example a key typed into Settings but not yet saved).

The filter is installed on both the console and file handlers.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import threading
from pathlib import Path

_SECRETS: set[str] = set()
_LOCK = threading.Lock()

_PATTERNS = [
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;\"']+"),
    re.compile(r"(?i)(x-goog-api-key\s*:\s*)[^\s,;\"']+"),
    re.compile(r"(?i)(xi-api-key\s*:\s*)[^\s,;\"']+"),
    re.compile(r"(?i)(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)([?&](?:key|api_key|apikey)=)[^&\s\"']+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}"),
]


def register_secret(value: str | None) -> None:
    """Remember a secret so it can be scrubbed from log output."""
    if not value or len(value) < 6:
        return
    with _LOCK:
        _SECRETS.add(value)


def clear_registered_secrets() -> None:
    with _LOCK:
        _SECRETS.clear()


def redact(text: str) -> str:
    if not text:
        return text
    with _LOCK:
        secrets = list(_SECRETS)
    for secret in secrets:
        if secret and secret in text:
            text = text.replace(secret, "[REDACTED]")
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + "[REDACTED]", text)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - stdlib API
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        if record.exc_info:
            exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_text = redact(exc_text)
        return True


def setup_logging(log_dir: Path, level: int = logging.INFO) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("apvg")
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:  # already configured (e.g. tests importing twice)
        return logger

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    redaction = RedactionFilter()

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(redaction)
    logger.addHandler(console)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.addFilter(redaction)
        logger.addHandler(file_handler)
    except OSError:
        # A read-only filesystem must not stop the app from starting.
        logger.warning("Could not open log file; continuing with console logging only.")

    return logger


def get_logger(name: str = "apvg") -> logging.Logger:
    if not name.startswith("apvg"):
        name = f"apvg.{name}"
    return logging.getLogger(name)
