from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final, cast

REDACTED: Final = "[REDACTED]"

_SENSITIVE_KEY_SUFFIXES: Final = (
    "_access_key",
    "_access_key_id",
    "_api_key",
    "_apikey",
    "_auth",
    "_authorization",
    "_bearer",
    "_client_secret",
    "_connection_string",
    "_cookie",
    "_credential",
    "_credentials",
    "_database_url",
    "_dsn",
    "_passphrase",
    "_password",
    "_passwd",
    "_private_key",
    "_redis_url",
    "_secret",
    "_session_key",
    "_signing_key",
    "_token",
    "_webhook_url",
)
_SENSITIVE_KEY_NAMES: Final = frozenset(
    {
        "access_key",
        "access_key_id",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "bearer",
        "client_secret",
        "connection_string",
        "cookie",
        "credential",
        "credentials",
        "database_url",
        "dsn",
        "passphrase",
        "password",
        "passwd",
        "private_key",
        "pwd",
        "redis_url",
        "secret",
        "session_key",
        "signing_key",
        "token",
        "webhook_url",
    }
)
_NON_SECRET_COUNTER_SUFFIXES: Final = (
    "_budget",
    "_configured",
    "_count",
    "_index",
    "_length",
    "_limit",
    "_present",
    "_usage",
)
_ASSIGNMENT_PATTERN: Final = re.compile(
    r"""(?ix)
    (
        \b(?:api[_-]?key|client[_-]?secret|password|passwd|private[_-]?key|
        proxy[_-]?token|refresh[_-]?token|secret|token)\b
        ["']?\s*[:=]\s*["']?
    )
    ([^"'\s,;&}]+)
    """
)
_AUTHORIZATION_HEADER_PATTERN: Final = re.compile(
    r"(?i)(\bauthorization\b\s*[:=]\s*)((?:Bearer|Basic|Bot)\s+)?([^\s,;]+)"
)
_AUTHORIZATION_PATTERN: Final = re.compile(r"(?i)\b(Bearer|Basic|Bot)\s+[^\s,;]+")
_KNOWN_CREDENTIAL_PATTERN: Final = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-(?:ant|proj)-[A-Za-z0-9_-]{12,}|"
    r"sk-[A-Za-z0-9_-]{20,}|"
    r"xox[a-z]-[A-Za-z0-9-]{12,}|"
    r"xapp-[A-Za-z0-9-]{12,}|"
    r"gh[opusr]_[A-Za-z0-9]{20,}|"
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r")"
)


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    if normalized in _SENSITIVE_KEY_NAMES:
        return True
    if normalized.endswith(_SENSITIVE_KEY_SUFFIXES):
        return True
    sensitive_prefix = normalized.startswith(("api_key_", "apikey_", "password_", "passwd_", "secret_", "token_"))
    return sensitive_prefix and not normalized.endswith(_NON_SECRET_COUNTER_SUFFIXES)


def _redact_text(value: str) -> str:
    value = _AUTHORIZATION_HEADER_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2) or ''}{REDACTED}",
        value,
    )
    value = _ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}{REDACTED}", value)
    value = _AUTHORIZATION_PATTERN.sub(lambda match: f"{match.group(1)} {REDACTED}", value)
    return _KNOWN_CREDENTIAL_PATTERN.sub(REDACTED, value)


def _redact_entire_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _redact_entire_value(item) for key, item in cast(Mapping[object, object], value).items()}
    if isinstance(value, list):
        return [_redact_entire_value(item) for item in cast(list[object], value)]
    if isinstance(value, tuple):
        return [_redact_entire_value(item) for item in cast(tuple[object, ...], value)]
    if value is None:
        return None
    return REDACTED


def redact_cli_payload(value: object) -> object:
    """Return a recursively sanitized copy suitable for terminal JSON output.

    This function is deliberately presentation-only. Callers must persist the
    original lifecycle payload before passing a value here so trusted evidence
    keeps the exact Arga response.
    """

    if isinstance(value, Mapping):
        redacted: dict[str, object] = {}
        for key, item in cast(Mapping[object, object], value).items():
            output_key = str(key)
            redacted[output_key] = _redact_entire_value(item) if _is_sensitive_key(key) else redact_cli_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_cli_payload(item) for item in cast(list[object], value)]
    if isinstance(value, tuple):
        return [redact_cli_payload(item) for item in cast(tuple[object, ...], value)]
    if isinstance(value, str):
        return _redact_text(value)
    return value
