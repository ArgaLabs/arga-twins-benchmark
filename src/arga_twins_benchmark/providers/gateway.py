from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Final, cast
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

import httpx

PROVIDER_API_TOOL_NAME: Final = "provider_api"

_ALLOWED_METHODS: Final = frozenset({"GET", "POST", "PATCH", "PUT", "DELETE"})
_CONTROL_PLANE_PREFIXES: Final = frozenset(
    {
        "_admin",
        "_control",
        "_twin",
        "_ui",
        "admin",
        "control",
        "control-plane",
        "control_plane",
        "inspect",
        "reset",
    }
)
_BLOCKED_REQUEST_HEADERS: Final = frozenset(
    {
        "authorization",
        "connection",
        "content-length",
        "content-type",
        "cookie",
        "host",
        "proxy-authorization",
        "proxy-connection",
        "set-cookie",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_SENSITIVE_RESPONSE_HEADERS: Final = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authenticate",
        "proxy-authorization",
        "set-cookie",
    }
)
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_GRAPHQL_DECLARATION = re.compile(r"\b(query|mutation|subscription)\b(?:\s+([_A-Za-z][_0-9A-Za-z]*))?")
_GRAPHQL_ROOT_FIELD = re.compile(r"\{\s*(?:@[A-Za-z_][_0-9A-Za-z]*(?:\([^)]*\))?\s*)*([_A-Za-z][_0-9A-Za-z]*)")
_MISSING: Final = object()


class ProviderGatewayConfigurationError(ValueError):
    """Raised when provisioned candidate access cannot form a safe gateway."""


@dataclass(frozen=True, slots=True)
class ProviderTraceRecord:
    sequence: int
    started_at: str
    requested_provider: str
    provider: str | None
    method: str | None
    path: str | None
    operation: str | None
    operation_type: str | None
    status_code: int | None
    latency_ms: int
    response_bytes: int
    truncated: bool
    error: str | None

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True, slots=True)
class _ProviderAccess:
    base_url: str
    env: Mapping[str, str]


class ProviderGateway:
    """A narrow candidate-facing gateway to provisioned provider data planes.

    The gateway never accepts an absolute request URL, never follows redirects,
    and never exposes candidate credentials in its result or trace. Every
    attempted call, including calls rejected before network I/O, is traced.
    """

    def __init__(
        self,
        provider_access: Mapping[str, Mapping[str, object]],
        provider_roles: Mapping[str, str] | None = None,
        *,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 262_144,
        max_request_bytes: int = 262_144,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ProviderGatewayConfigurationError("timeout_seconds must be positive")
        if max_response_bytes < 1:
            raise ProviderGatewayConfigurationError("max_response_bytes must be positive")
        if max_request_bytes < 1:
            raise ProviderGatewayConfigurationError("max_request_bytes must be positive")

        self._providers = _validate_provider_access(provider_access)
        self._roles = _validate_provider_roles(provider_roles or {}, self._providers)
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._max_request_bytes = max_request_bytes
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)
        self._owns_client = client is None
        self._trace_records: list[ProviderTraceRecord] = []

        provider_tokens = sorted(set(self._providers) | set(self._roles))
        self._tool_definition: dict[str, object] = {
            "name": PROVIDER_API_TOOL_NAME,
            "description": (
                "Call an API on one of this task's provisioned service twins. Use only a provider or provider role "
                "listed in the schema and a relative path beginning with '/'. Absolute URLs and twin control-plane "
                "paths are blocked. Authentication is supplied automatically. JSON bodies are the default; Stripe "
                "form bodies are selected automatically, or body_encoding can be set explicitly."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": provider_tokens,
                        "description": "Provisioned provider name or task-specific provider role.",
                    },
                    "method": {
                        "type": "string",
                        "enum": sorted(_ALLOWED_METHODS),
                    },
                    "path": {
                        "type": "string",
                        "description": "Relative provider API path beginning with '/'; it may include a query string.",
                    },
                    "query": {
                        "type": "object",
                        "description": "Optional query parameters. Keys are encoded in deterministic sorted order.",
                        "additionalProperties": {
                            "anyOf": [
                                {"type": "string"},
                                {"type": "number"},
                                {"type": "integer"},
                                {"type": "boolean"},
                                {"type": "array", "items": {"type": ["string", "number", "integer", "boolean"]}},
                            ]
                        },
                    },
                    "body": {
                        "description": "Optional JSON value or form field object for the request body.",
                    },
                    "body_encoding": {
                        "type": "string",
                        "enum": ["json", "form"],
                        "description": "Optional body encoding. Defaults to form for Stripe and JSON otherwise.",
                    },
                    "headers": {
                        "type": "object",
                        "description": (
                            "Optional non-authentication request headers, for example Idempotency-Key or If-Match."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["provider", "method", "path"],
                "additionalProperties": False,
            },
        }

    @property
    def tool_definition(self) -> dict[str, object]:
        return cast(dict[str, object], _copy_json(self._tool_definition))

    @property
    def trace_records(self) -> tuple[ProviderTraceRecord, ...]:
        return tuple(self._trace_records)

    def clear_trace_records(self) -> None:
        self._trace_records.clear()

    async def __aenter__(self) -> ProviderGateway:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def execute(self, tool_input: Mapping[str, object]) -> dict[str, object]:
        started = time.monotonic()
        started_at = datetime.now(UTC).isoformat()
        requested_provider = _string_value(tool_input.get("provider")) or ""
        resolved_provider = self._roles.get(requested_provider, requested_provider) or None
        method = (_string_value(tool_input.get("method")) or "").upper() or None
        raw_path = _string_value(tool_input.get("path"))
        operation, operation_type = _infer_graphql_operation(tool_input.get("body"))
        effective_path: str | None = raw_path

        try:
            resolved_provider, provider = self._resolve_provider(requested_provider)
            checked_method = _validate_method(method)
            path, path_query = _validate_relative_path(raw_path)
            query_pairs = _merge_query_pairs(path_query, tool_input.get("query"))
            safe_headers = _validate_custom_headers(tool_input.get("headers"))
            body_encoding = _validate_body_encoding(tool_input.get("body_encoding"), resolved_provider)
            request_content, body_headers, request_size = _prepare_body(
                body=tool_input.get("body", _MISSING),
                body_encoding=body_encoding,
            )
            if request_size > self._max_request_bytes:
                raise ValueError(f"request body exceeds the {self._max_request_bytes}-byte gateway limit")

            headers = provider_request_headers(resolved_provider, provider.env)
            headers.update(body_headers)
            headers.update(safe_headers)
            headers.setdefault("User-Agent", "arga-twins-benchmark/0.1")
            encoded_query = urlencode(query_pairs)
            request_path = f"{path}?{encoded_query}" if encoded_query else path
            request = self._client.build_request(
                checked_method,
                f"{provider.base_url}{request_path}",
                headers=headers,
                timeout=self._timeout_seconds,
                content=request_content,
            )
            effective_path = _effective_relative_path(request.url)
            response = await self._client.send(request, stream=True, follow_redirects=False)
            try:
                response_bytes, truncated = await _read_bounded(response, self._max_response_bytes)
            finally:
                await response.aclose()

            response_body = _decode_body(response_bytes, response.headers.get("content-type"), truncated=truncated)
            latency_ms = _elapsed_ms(started)
            trace = self._append_trace(
                started_at=started_at,
                requested_provider=requested_provider,
                provider=resolved_provider,
                method=checked_method,
                path=effective_path,
                operation=operation,
                operation_type=operation_type,
                status_code=response.status_code,
                latency_ms=latency_ms,
                response_bytes=len(response_bytes),
                truncated=truncated,
                error=None,
            )
            return {
                "ok": response.is_success,
                "requested_provider": requested_provider,
                "provider": resolved_provider,
                "method": checked_method,
                "path": effective_path,
                "status_code": response.status_code,
                "headers": _safe_response_headers(response.headers),
                "body": response_body,
                "truncated": truncated,
                "error": None,
                "trace": trace.to_dict(),
            }
        except (httpx.HTTPError, ValueError) as exc:
            latency_ms = _elapsed_ms(started)
            error = _public_error(exc)
            trace = self._append_trace(
                started_at=started_at,
                requested_provider=requested_provider,
                provider=resolved_provider if resolved_provider in self._providers else None,
                method=method,
                path=effective_path,
                operation=operation,
                operation_type=operation_type,
                status_code=None,
                latency_ms=latency_ms,
                response_bytes=0,
                truncated=False,
                error=error,
            )
            return {
                "ok": False,
                "requested_provider": requested_provider,
                "provider": resolved_provider if resolved_provider in self._providers else None,
                "method": method,
                "path": effective_path,
                "status_code": None,
                "headers": {},
                "body": None,
                "truncated": False,
                "error": error,
                "trace": trace.to_dict(),
            }

    def _resolve_provider(self, requested_provider: str) -> tuple[str, _ProviderAccess]:
        if not requested_provider:
            raise ValueError("provider must be a non-empty string")
        resolved = self._roles.get(requested_provider, requested_provider)
        try:
            return resolved, self._providers[resolved]
        except KeyError as exc:
            allowed = ", ".join(sorted(set(self._providers) | set(self._roles)))
            raise ValueError(f"unknown provider {requested_provider!r}; allowed providers: {allowed}") from exc

    def _append_trace(
        self,
        *,
        started_at: str,
        requested_provider: str,
        provider: str | None,
        method: str | None,
        path: str | None,
        operation: str | None,
        operation_type: str | None,
        status_code: int | None,
        latency_ms: int,
        response_bytes: int,
        truncated: bool,
        error: str | None,
    ) -> ProviderTraceRecord:
        trace = ProviderTraceRecord(
            sequence=len(self._trace_records) + 1,
            started_at=started_at,
            requested_provider=requested_provider,
            provider=provider,
            method=method,
            path=path,
            operation=operation,
            operation_type=operation_type,
            status_code=status_code,
            latency_ms=latency_ms,
            response_bytes=response_bytes,
            truncated=truncated,
            error=error,
        )
        self._trace_records.append(trace)
        return trace


def _validate_provider_access(
    provider_access: Mapping[str, Mapping[str, object]],
) -> dict[str, _ProviderAccess]:
    if not provider_access:
        raise ProviderGatewayConfigurationError("provider_access cannot be empty")

    providers: dict[str, _ProviderAccess] = {}
    for raw_name, raw_access in provider_access.items():
        name = raw_name.strip()
        if not name or name != raw_name:
            raise ProviderGatewayConfigurationError(f"invalid provider name {raw_name!r}")
        base_url_value = raw_access.get("base_url")
        if not isinstance(base_url_value, str):
            raise ProviderGatewayConfigurationError(f"provider {name!r} is missing a string base_url")
        base_url = _validate_base_url(base_url_value, provider=name)
        raw_env = raw_access.get("env", {})
        if not isinstance(raw_env, Mapping):
            raise ProviderGatewayConfigurationError(f"provider {name!r} env must be a mapping")
        env: dict[str, str] = {}
        env_mapping = cast(Mapping[object, object], raw_env)
        for raw_key, raw_value in env_mapping.items():
            if not isinstance(raw_key, str) or not isinstance(raw_value, str):
                raise ProviderGatewayConfigurationError(f"provider {name!r} env must contain only strings")
            env[raw_key] = raw_value
        providers[name] = _ProviderAccess(base_url=base_url, env=env)
    return providers


def _validate_provider_roles(
    provider_roles: Mapping[str, str],
    providers: Mapping[str, _ProviderAccess],
) -> dict[str, str]:
    roles: dict[str, str] = {}
    for raw_role, provider in provider_roles.items():
        role = raw_role.strip()
        if not role or role != raw_role:
            raise ProviderGatewayConfigurationError(f"invalid provider role {raw_role!r}")
        if provider not in providers:
            raise ProviderGatewayConfigurationError(f"provider role {role!r} resolves to unknown provider {provider!r}")
        if role in providers and role != provider:
            raise ProviderGatewayConfigurationError(
                f"provider role {role!r} conflicts with a provisioned provider name"
            )
        roles[role] = provider
    return roles


def _validate_base_url(base_url: str, *, provider: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProviderGatewayConfigurationError(f"provider {provider!r} has an invalid HTTP(S) base_url")
    if parsed.username is not None or parsed.password is not None:
        raise ProviderGatewayConfigurationError(f"provider {provider!r} base_url cannot contain credentials")
    if parsed.query or parsed.fragment:
        raise ProviderGatewayConfigurationError(f"provider {provider!r} base_url cannot contain query or fragment")
    if parsed.path not in {"", "/"}:
        raise ProviderGatewayConfigurationError(f"provider {provider!r} base_url cannot contain a path")
    return base_url.rstrip("/")


def _validate_method(method: str | None) -> str:
    if method is None or method not in _ALLOWED_METHODS:
        allowed = ", ".join(sorted(_ALLOWED_METHODS))
        raise ValueError(f"method must be one of: {allowed}")
    return method


def _validate_relative_path(raw_path: str | None) -> tuple[str, list[tuple[str, str]]]:
    if raw_path is None or not raw_path:
        raise ValueError("path must be a non-empty string")
    if any(character in raw_path for character in ("\r", "\n", "\x00")):
        raise ValueError("path contains forbidden control characters")
    parsed = urlsplit(raw_path)
    if parsed.scheme or parsed.netloc:
        raise ValueError("absolute or network-path URLs are forbidden")
    if parsed.fragment:
        raise ValueError("URL fragments are forbidden")
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        raise ValueError("path must be relative to the provisioned provider and begin with exactly one '/'")

    decoded_path = parsed.path
    for _ in range(5):
        next_path = unquote(decoded_path)
        if next_path == decoded_path:
            break
        decoded_path = next_path
    normalized_for_checks = decoded_path.replace("\\", "/")
    if normalized_for_checks.startswith("//"):
        raise ValueError("network-path URLs are forbidden")
    segments = [segment.casefold() for segment in normalized_for_checks.split("/") if segment]
    if any(segment in {".", ".."} for segment in segments):
        raise ValueError("path traversal is forbidden")
    if segments and segments[0] in _CONTROL_PLANE_PREFIXES:
        raise ValueError(f"provider control-plane path '/{segments[0]}' is forbidden")
    return parsed.path, parse_qsl(parsed.query, keep_blank_values=True)


def _merge_query_pairs(path_query: list[tuple[str, str]], raw_query: object) -> list[tuple[str, str]]:
    pairs = list(path_query)
    if raw_query is not None:
        if not isinstance(raw_query, Mapping):
            raise ValueError("query must be an object")
        query_mapping = cast(Mapping[object, object], raw_query)
        for raw_key, raw_value in query_mapping.items():
            if not isinstance(raw_key, str) or not raw_key:
                raise ValueError("query parameter names must be non-empty strings")
            if isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes, bytearray)):
                values = cast(Sequence[object], raw_value)
                for item in values:
                    pairs.append((raw_key, _query_scalar(item)))
            else:
                pairs.append((raw_key, _query_scalar(raw_value)))
    return sorted(pairs, key=lambda pair: (pair[0], pair[1]))


def _query_scalar(value: object) -> str:
    if value is None or isinstance(value, (Mapping, bytes, bytearray)):
        raise ValueError("query values must be strings, numbers, booleans, or arrays of those values")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    raise ValueError("query values must be strings, numbers, booleans, or arrays of those values")


def _validate_custom_headers(raw_headers: object) -> dict[str, str]:
    if raw_headers is None:
        return {}
    if not isinstance(raw_headers, Mapping):
        raise ValueError("headers must be an object")
    headers: dict[str, str] = {}
    header_mapping = cast(Mapping[object, object], raw_headers)
    for raw_name, raw_value in header_mapping.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise ValueError("request header names and values must be strings")
        name = raw_name.strip()
        lowered = name.casefold()
        if not name or not _HEADER_NAME.fullmatch(name):
            raise ValueError(f"invalid request header name {raw_name!r}")
        if lowered in _BLOCKED_REQUEST_HEADERS or lowered.startswith(("x-arga-", "x-admin-", "x-twin-")):
            raise ValueError(f"request header {name!r} cannot be supplied by the candidate")
        if "\r" in raw_value or "\n" in raw_value:
            raise ValueError(f"request header {name!r} contains forbidden control characters")
        headers[name] = raw_value
    return headers


def _validate_body_encoding(raw_encoding: object, provider: str) -> str:
    if raw_encoding is None:
        return "form" if provider == "stripe" else "json"
    if not isinstance(raw_encoding, str) or raw_encoding not in {"json", "form"}:
        raise ValueError("body_encoding must be 'json' or 'form'")
    return raw_encoding


def _prepare_body(*, body: object, body_encoding: str) -> tuple[bytes | None, dict[str, str], int]:
    if body is _MISSING:
        return None, {}, 0
    if body_encoding == "json":
        try:
            encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        except (TypeError, ValueError) as exc:
            raise ValueError("body must be JSON serializable") from exc
        return encoded, {"Content-Type": "application/json"}, len(encoded)
    if not isinstance(body, Mapping):
        raise ValueError("form-encoded body must be an object")
    form_pairs = _form_pairs(cast(Mapping[object, object], body))
    encoded = urlencode(form_pairs).encode()
    return encoded, {"Content-Type": "application/x-www-form-urlencoded"}, len(encoded)


def _form_pairs(body: Mapping[object, object], prefix: str = "") -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    sortable: list[tuple[str, object]] = []
    for raw_key, value in body.items():
        if not isinstance(raw_key, str) or not raw_key:
            raise ValueError("form field names must be non-empty strings")
        sortable.append((raw_key, value))
    for key, value in sorted(sortable, key=lambda item: item[0]):
        field = f"{prefix}[{key}]" if prefix else key
        if isinstance(value, Mapping):
            pairs.extend(_form_pairs(cast(Mapping[object, object], value), prefix=field))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            values = cast(Sequence[object], value)
            for item in values:
                if isinstance(item, (Mapping, Sequence)) and not isinstance(item, str):
                    raise ValueError("nested arrays and objects are not supported inside form arrays")
                pairs.append((f"{field}[]", _form_scalar(item)))
        else:
            pairs.append((field, _form_scalar(value)))
    return pairs


def _form_scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    raise ValueError("form values must be strings, numbers, booleans, nulls, objects, or scalar arrays")


def _provider_headers(provider: str, env: Mapping[str, str]) -> dict[str, str]:
    token = _first_env_value(env, _PROVIDER_ENV_KEYS.get(provider, ()))
    if provider == "github":
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token or 'ghp_scenario_seed'}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    if provider == "gitlab":
        return {"PRIVATE-TOKEN": token or "glpat-gitlab-twin-token"}
    if provider == "gmail":
        return {"Authorization": f"Bearer {token or 'ya29.gmail-twin-owner'}"}
    if provider == "google_calendar":
        return {"Authorization": f"Bearer {token or 'test-token'}"}
    if provider == "google_drive":
        return {"Authorization": f"Bearer {token or 'ya29.drive-twin-owner'}"}
    if provider == "jira":
        return {"Authorization": f"Bearer {token or 'jira_default_seed_token'}"}
    if provider == "linear":
        return {"Authorization": token or "lin_api_twin_owner_personal_key_0001"}
    if provider == "notion":
        return {
            "Authorization": f"Bearer {token or 'secret_notion-twin_seed'}",
            "Notion-Version": "2026-03-11",
        }
    if provider == "slack":
        return {"Authorization": f"Bearer {token or 'xoxb-F9SXMECOSFOGYR3XKXWN'}"}
    if provider == "stripe":
        return {"Authorization": f"Bearer {token or 'sk_test_twin_scenario'}"}
    if provider == "discord":
        return {"Authorization": f"Bot {token or 'fake-bot-token'}"}
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def provider_request_headers(provider: str, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return provider-native headers for a provisioned benchmark twin."""

    return _provider_headers(provider, env or {})


_PROVIDER_ENV_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "github": ("GITHUB_TOKEN", "GH_TOKEN"),
    "gitlab": ("GITLAB_TOKEN", "GITLAB_PRIVATE_TOKEN"),
    "gmail": ("GMAIL_TOKEN", "GMAIL_ACCESS_TOKEN", "GOOGLE_ACCESS_TOKEN", "GOOGLE_OAUTH_ACCESS_TOKEN"),
    "google_calendar": (
        "GOOGLE_CALENDAR_TOKEN",
        "GOOGLE_CALENDAR_ACCESS_TOKEN",
        "GOOGLE_ACCESS_TOKEN",
        "GOOGLE_OAUTH_ACCESS_TOKEN",
    ),
    "google_drive": (
        "GOOGLE_DRIVE_TOKEN",
        "GOOGLE_DRIVE_ACCESS_TOKEN",
        "GOOGLE_ACCESS_TOKEN",
        "GOOGLE_OAUTH_ACCESS_TOKEN",
    ),
    "jira": ("JIRA_TOKEN", "JIRA_API_TOKEN"),
    "linear": ("LINEAR_API_KEY", "LINEAR_TOKEN"),
    "notion": ("NOTION_TOKEN", "NOTION_API_KEY"),
    "slack": ("SLACK_BOT_TOKEN", "SLACK_TOKEN"),
    "stripe": ("STRIPE_API_KEY", "STRIPE_SECRET_KEY"),
    "discord": ("DISCORD_TOKEN", "DISCORD_BOT_TOKEN"),
}


def _first_env_value(env: Mapping[str, str], names: Sequence[str]) -> str | None:
    for name in names:
        value = env.get(name)
        if value:
            return value
    return None


async def _read_bounded(response: httpx.Response, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    truncated = False
    async for chunk in response.aiter_bytes():
        remaining = limit - total
        if remaining <= 0:
            truncated = True
            break
        chunks.append(chunk[:remaining])
        total += min(len(chunk), remaining)
        if len(chunk) > remaining:
            truncated = True
            break
    return b"".join(chunks), truncated


def _decode_body(content: bytes, content_type: str | None, *, truncated: bool) -> object:
    if not content:
        return None
    normalized_type = (content_type or "").split(";", 1)[0].strip().casefold()
    if not truncated and (normalized_type == "application/json" or normalized_type.endswith("+json")):
        try:
            return cast(object, json.loads(content))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    if (
        normalized_type.startswith("text/")
        or normalized_type in {"", "application/graphql", "application/xml", "application/x-www-form-urlencoded"}
        or normalized_type.endswith("+xml")
    ):
        return content.decode("utf-8", errors="replace")
    return {"encoding": "base64", "data": base64.b64encode(content).decode("ascii")}


def _safe_response_headers(headers: httpx.Headers) -> dict[str, str]:
    safe: dict[str, str] = {}
    for name, value in sorted(headers.multi_items()):
        lowered = name.casefold()
        if lowered in _SENSITIVE_RESPONSE_HEADERS or lowered.startswith(("x-arga-", "x-admin-", "x-twin-")):
            continue
        if len(safe) >= 50:
            break
        safe[lowered] = value[:4096]
    return safe


def _effective_relative_path(url: httpx.URL) -> str:
    return url.raw_path.decode("ascii", errors="replace")


def _infer_graphql_operation(body: object) -> tuple[str | None, str | None]:
    if not isinstance(body, Mapping):
        return None, None
    body_mapping = cast(Mapping[object, object], body)
    operation_name = body_mapping.get("operationName")
    query = body_mapping.get("query")
    if not isinstance(query, str):
        return _string_value(operation_name), None
    declaration = _GRAPHQL_DECLARATION.search(query)
    operation_type = declaration.group(1) if declaration else None
    root_field = _GRAPHQL_ROOT_FIELD.search(query)
    if root_field:
        return root_field.group(1), operation_type
    if isinstance(operation_name, str) and operation_name.strip():
        return operation_name.strip(), operation_type
    return (declaration.group(2) if declaration and declaration.group(2) else None), operation_type


def _public_error(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "provider request timed out"
    if isinstance(exc, httpx.HTTPError):
        return f"provider request failed: {exc.__class__.__name__}"
    return str(exc)


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _string_value(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _copy_json(value: object) -> object:
    return json.loads(json.dumps(value))
