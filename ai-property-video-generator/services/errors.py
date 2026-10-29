"""Central error taxonomy.

Every failure the user can encounter is raised as an ``AppError`` (or subclass)
carrying three things:

* a stable machine ``code`` the frontend can branch on,
* a plain-English ``message`` safe to show a non-technical user,
* an optional ``hint`` describing what to do next.

Technical diagnostics live in ``log_detail`` and go to the server log only; they
are never serialised into an API response. This is what keeps API keys and
provider internals out of the browser.
"""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass
class AppError(Exception):
    code: str = "error"
    message: str = "Something went wrong."
    hint: str | None = None
    field: str | None = None
    retryable: bool = False
    http_status: int = 400
    details: dict[str, Any] | None = None
    log_detail: str | None = None

    def __post_init__(self) -> None:  # Exception stores a message for tracebacks
        super().__init__(self.message)

    def to_public(self) -> dict[str, Any]:
        """Serialisable shape for JSON responses. Never includes log_detail."""
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.hint:
            payload["hint"] = self.hint
        if self.field:
            payload["field"] = self.field
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(AppError):
    def __init__(self, message: str, *, field: str | None = None, hint: str | None = None,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(code="validation_error", message=message, field=field, hint=hint,
                         http_status=400, details=details)


class SecurityError(AppError):
    def __init__(self, message: str = "This action is not allowed from here.",
                 log_detail: str | None = None) -> None:
        super().__init__(code="forbidden", message=message, http_status=403,
                         log_detail=log_detail)


class NotFoundError(AppError):
    def __init__(self, message: str = "That item could not be found.") -> None:
        super().__init__(code="not_found", message=message, http_status=404)


class ConflictError(AppError):
    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(code="busy", message=message, hint=hint, http_status=409,
                         retryable=True)


# --------------------------------------------------------------------------- #
# Provider (LLM / TTS / cloud) failures
# --------------------------------------------------------------------------- #
class ProviderError(AppError):
    provider: str | None = None


class ProviderAuthError(ProviderError):
    def __init__(self, provider: str, log_detail: str | None = None) -> None:
        super().__init__(
            code="invalid_credentials",
            message=f"The {provider} API key was rejected.",
            hint="Open Settings, check the key, save it again, then use Test connection.",
            retryable=False,
            http_status=400,
            log_detail=log_detail,
        )
        self.provider = provider


class ProviderPermissionError(ProviderError):
    def __init__(self, provider: str, log_detail: str | None = None) -> None:
        super().__init__(
            code="provider_permission",
            message=f"The {provider} key does not have permission for this request.",
            hint="Check the key's scopes or plan, or pick a different model.",
            http_status=400,
            log_detail=log_detail,
        )
        self.provider = provider


class ProviderRateLimitError(ProviderError):
    def __init__(self, provider: str, log_detail: str | None = None) -> None:
        super().__init__(
            code="rate_limited",
            message=f"{provider} is temporarily rate limiting requests.",
            hint="Wait a few seconds and try again.",
            retryable=True,
            http_status=429,
            log_detail=log_detail,
        )
        self.provider = provider


class ProviderQuotaError(ProviderError):
    def __init__(self, provider: str, log_detail: str | None = None) -> None:
        super().__init__(
            code="quota_exhausted",
            message=f"The {provider} quota or billing limit has been reached.",
            hint="Add credit or use template scripts / a different engine instead.",
            retryable=False,
            http_status=402,
            log_detail=log_detail,
        )
        self.provider = provider


class ProviderNetworkError(ProviderError):
    def __init__(self, provider: str, log_detail: str | None = None) -> None:
        super().__init__(
            code="network_error",
            message=f"Could not reach {provider}.",
            hint="Check your internet connection and try again.",
            retryable=True,
            http_status=503,
            log_detail=log_detail,
        )
        self.provider = provider


class ProviderUnsupportedError(ProviderError):
    def __init__(self, provider: str, what: str = "model", log_detail: str | None = None) -> None:
        super().__init__(
            code="unsupported_choice",
            message=f"{provider} does not recognise that {what}.",
            hint=f"Choose a different {what} in Settings.",
            http_status=400,
            log_detail=log_detail,
        )
        self.provider = provider


class MalformedAIResponse(AppError):
    def __init__(self, log_detail: str | None = None) -> None:
        super().__init__(
            code="malformed_ai_response",
            message="The AI returned a response the app could not use.",
            hint="Try again, or let the app use its built-in template script.",
            retryable=True,
            http_status=502,
            log_detail=log_detail,
        )


class TTSError(AppError):
    def __init__(self, message: str, *, hint: str | None = None, retryable: bool = True,
                 log_detail: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(code="tts_failed", message=message, hint=hint, retryable=retryable,
                         http_status=502, log_detail=log_detail, details=details)


# --------------------------------------------------------------------------- #
# Local media / environment failures
# --------------------------------------------------------------------------- #
class ImageError(AppError):
    def __init__(self, message: str, *, field: str = "images", hint: str | None = None) -> None:
        super().__init__(code="invalid_image", message=message, field=field, hint=hint)


class MusicError(AppError):
    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(code="invalid_music", message=message, field="music", hint=hint)


class RenderError(AppError):
    def __init__(self, message: str, *, hint: str | None = None,
                 log_detail: str | None = None) -> None:
        super().__init__(code="render_failed", message=message, hint=hint, retryable=True,
                         http_status=500, log_detail=log_detail)


class DiskSpaceError(AppError):
    def __init__(self, needed_mb: int, free_mb: int) -> None:
        super().__init__(
            code="insufficient_disk",
            message="There is not enough free disk space to render this video.",
            hint=f"Free up about {needed_mb} MB (currently {free_mb} MB available) and retry.",
            http_status=507,
        )


class DependencyError(AppError):
    def __init__(self, message: str, hint: str | None = None,
                 log_detail: str | None = None) -> None:
        super().__init__(code="missing_dependency", message=message, hint=hint,
                         http_status=500, log_detail=log_detail)


class CancelledError(AppError):
    def __init__(self) -> None:
        super().__init__(code="cancelled", message="Generation was cancelled.",
                         http_status=409)


class SettingsError(AppError):
    def __init__(self, message: str, *, field: str | None = None,
                 hint: str | None = None) -> None:
        super().__init__(code="settings_error", message=message, field=field, hint=hint,
                         http_status=400)


# --------------------------------------------------------------------------- #
# HTTP status -> AppError mapping shared by the LLM and TTS clients
# --------------------------------------------------------------------------- #
def error_from_http(provider: str, status: int, body: str = "") -> AppError:
    """Map a provider HTTP status onto our taxonomy.

    Deliberately conservative: we only label something "quota" when the provider
    actually says so, because telling a user their quota is exhausted when the key
    is merely invalid sends them down the wrong path.
    """
    lowered = (body or "").lower()
    detail = f"{provider} HTTP {status}: {body[:400]}"

    if status in (401,):
        return ProviderAuthError(provider, detail)
    if status == 403:
        if "quota" in lowered or "billing" in lowered:
            return ProviderQuotaError(provider, detail)
        return ProviderPermissionError(provider, detail)
    if status == 404:
        return ProviderUnsupportedError(provider, "model or endpoint", detail)
    if status == 429:
        if "quota" in lowered and "rate" not in lowered:
            return ProviderQuotaError(provider, detail)
        return ProviderRateLimitError(provider, detail)
    if status in (402,):
        return ProviderQuotaError(provider, detail)
    if status >= 500:
        return ProviderNetworkError(provider, detail)
    if status == 400:
        # 400 with an auth-ish body is usually a malformed/absent key.
        if any(token in lowered for token in ("api key", "api_key", "credential", "unauthorized")):
            return ProviderAuthError(provider, detail)
        return ProviderUnsupportedError(provider, "model", detail)
    return ProviderError(code="provider_error", message=f"{provider} returned an error.",
                         hint="Try again or switch providers in Settings.",
                         http_status=502, log_detail=detail)


def classify_request_exception(provider: str, exc: Exception) -> AppError:
    """Turn a requests/network exception into a friendly AppError."""
    import requests

    if isinstance(exc, requests.exceptions.Timeout):
        return ProviderNetworkError(provider, f"{provider} timed out: {exc}")
    if isinstance(exc, requests.exceptions.ConnectionError):
        return ProviderNetworkError(provider, f"{provider} connection error: {exc}")
    if isinstance(exc, requests.exceptions.RequestException):
        return ProviderNetworkError(provider, f"{provider} request failed: {exc}")
    return ProviderError(code="provider_error", message=f"{provider} call failed.",
                         hint="Try again, or use the built-in template script.",
                         http_status=502, log_detail=repr(exc))
