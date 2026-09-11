"""Editable DeepSeek settings used by :mod:`llm_mutation`.

The mutation planner imports this module by name. Environment variables take
precedence at import time so a credential and deployment-specific limits do not
need to be committed. The checked-in scaffold intentionally keeps the local API
key and model blank: an enabled advisor with either field blank performs a
logged no-op and never sends an HTTP request.
"""

from __future__ import annotations

import os
from typing import Any


# Editable local fallbacks. Keep both values blank in version-controlled code.
# For normal use prefer DEEPSEEK_API_KEY and DEEPSEEK_MODEL environment
# variables; they take precedence over these values. The planner requires both
# fields before it can make a network request.
LOCAL_DEEPSEEK_API_KEY = "YOUR_API_KEY"
LOCAL_DEEPSEEK_MODEL = "deepseek-flash"


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _as_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(_env(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _as_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(_env(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _endpoint() -> str:
    explicit = os.getenv("DEEPSEEK_ENDPOINT") or os.getenv("ENDPOINT") or os.getenv("API_URL")
    if explicit:
        return explicit.strip()
    base_url = os.getenv("DEEPSEEK_BASE_URL") or os.getenv("BASE_URL")
    if base_url:
        return base_url.rstrip("/") + "/chat/completions"
    return "https://api.deepseek.com/chat/completions"


# These names intentionally mirror the aliases accepted by
# ``DeepSeekSettings.from_sources``. Keep the credential and model blank in
# the checked-in scaffold; set the LOCAL_* fallback values or environment
# variables when DeepSeek is ready to be enabled.
DEFAULT_ENDPOINT = _endpoint()
DEFAULT_MODEL = _env("DEEPSEEK_MODEL", LOCAL_DEEPSEEK_MODEL)
DEFAULT_TIMEOUT_SECONDS = _as_float("DEEPSEEK_TIMEOUT_SECONDS", 45.0, 1.0, 600.0)
DEFAULT_MAX_RETRIES = _as_int("DEEPSEEK_MAX_RETRIES", 1, 0, 5)
DEFAULT_TEMPERATURE = _as_float("DEEPSEEK_TEMPERATURE", 0.1, 0.0, 2.0)
DEFAULT_MAX_TOKENS = _as_int("DEEPSEEK_MAX_TOKENS", 2048, 1, 65536)
DEFAULT_MAX_ACTIONS = _as_int("DEEPSEEK_MAX_ACTIONS", 16, 0, 4096)
DEFAULT_MAX_RESPONSE_BYTES = _as_int(
    "DEEPSEEK_MAX_RESPONSE_BYTES", 1_000_000, 1024, 20_000_000
)
DEFAULT_MAX_COMPONENT_GENERATION_ATTEMPTS = _as_int(
    "DEEPSEEK_MAX_COMPONENT_GENERATION_ATTEMPTS", 3, 1, 3
)


# Enabled is intentionally true by default so supplying a key and model opts
# in without another edit. Blank credentials/model remain the safety gate and
# cause a no-op before any HTTP client is invoked.
DEEPSEEK_ENABLED = _as_bool(_env("DEEPSEEK_ENABLED", "true"))
# DEEPSEEK_ENABLED = _as_bool(_env("DEEPSEEK_ENABLED", "false"))
DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY", LOCAL_DEEPSEEK_API_KEY)
DEEPSEEK_ENDPOINT = DEFAULT_ENDPOINT
DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = DEFAULT_MODEL
DEEPSEEK_TIMEOUT_SECONDS = DEFAULT_TIMEOUT_SECONDS
DEEPSEEK_MAX_RETRIES = DEFAULT_MAX_RETRIES
DEEPSEEK_TEMPERATURE = DEFAULT_TEMPERATURE
DEEPSEEK_MAX_TOKENS = DEFAULT_MAX_TOKENS
DEEPSEEK_MAX_ACTIONS = DEFAULT_MAX_ACTIONS
DEEPSEEK_MAX_RESPONSE_BYTES = DEFAULT_MAX_RESPONSE_BYTES
DEEPSEEK_MAX_COMPONENT_GENERATION_ATTEMPTS = DEFAULT_MAX_COMPONENT_GENERATION_ATTEMPTS

# Short aliases are useful when a researcher edits this file directly and are
# also recognized by the planner's compatibility loader.
ENABLED = DEEPSEEK_ENABLED
API_KEY = DEEPSEEK_API_KEY
ENDPOINT = DEEPSEEK_ENDPOINT
MODEL = DEEPSEEK_MODEL
TIMEOUT_SECONDS = DEEPSEEK_TIMEOUT_SECONDS
MAX_RETRIES = DEEPSEEK_MAX_RETRIES
TEMPERATURE = DEEPSEEK_TEMPERATURE
MAX_TOKENS = DEEPSEEK_MAX_TOKENS
MAX_ACTIONS = DEEPSEEK_MAX_ACTIONS
MAX_RESPONSE_BYTES = DEEPSEEK_MAX_RESPONSE_BYTES
MAX_COMPONENT_GENERATION_ATTEMPTS = DEEPSEEK_MAX_COMPONENT_GENERATION_ATTEMPTS

# The mapping is kept explicit for callers that prefer configuration objects.
# It contains the same values as the module aliases; environment resolution has
# already happened above.
DEEPSEEK_CONFIG = {
    "enabled": DEEPSEEK_ENABLED,
    "api_key": DEEPSEEK_API_KEY,
    "model": DEEPSEEK_MODEL,
    "endpoint": DEEPSEEK_ENDPOINT,
    "base_url": DEEPSEEK_BASE_URL,
    "timeout_seconds": DEEPSEEK_TIMEOUT_SECONDS,
    "max_retries": DEEPSEEK_MAX_RETRIES,
    "temperature": DEEPSEEK_TEMPERATURE,
    "max_tokens": DEEPSEEK_MAX_TOKENS,
    "max_actions": DEEPSEEK_MAX_ACTIONS,
    "max_response_bytes": DEEPSEEK_MAX_RESPONSE_BYTES,
    "max_component_generation_attempts": DEEPSEEK_MAX_COMPONENT_GENERATION_ATTEMPTS,
}


def get_deepseek_config() -> dict[str, Any]:
    """Return a copy suitable for constructing ``DeepSeekSettings``."""

    return dict(DEEPSEEK_CONFIG)


def get_public_deepseek_config() -> dict[str, Any]:
    """Return settings safe to include in logs (the API key is redacted)."""

    result = get_deepseek_config()
    result.pop("api_key", None)
    result["api_key_configured"] = bool(DEEPSEEK_API_KEY)
    return result


__all__ = [
    "LOCAL_DEEPSEEK_API_KEY",
    "LOCAL_DEEPSEEK_MODEL",
    "DEFAULT_ENDPOINT",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MAX_ACTIONS",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_MAX_COMPONENT_GENERATION_ATTEMPTS",
    "DEEPSEEK_ENABLED",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_ENDPOINT",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "DEEPSEEK_TIMEOUT_SECONDS",
    "DEEPSEEK_MAX_RETRIES",
    "DEEPSEEK_TEMPERATURE",
    "DEEPSEEK_MAX_TOKENS",
    "DEEPSEEK_MAX_ACTIONS",
    "DEEPSEEK_MAX_RESPONSE_BYTES",
    "DEEPSEEK_MAX_COMPONENT_GENERATION_ATTEMPTS",
    "MAX_ACTIONS",
    "MAX_COMPONENT_GENERATION_ATTEMPTS",
    "DEEPSEEK_CONFIG",
    "get_deepseek_config",
    "get_public_deepseek_config",
]
