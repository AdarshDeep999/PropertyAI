"""Production entry point.

    waitress-serve --listen=0.0.0.0:8000 wsgi:app

Set APP_USERNAME and APP_PASSWORD before exposing this beyond your machine;
the app then requires HTTP Basic authentication on every request.
"""

from __future__ import annotations

from app import app

__all__ = ["app"]
