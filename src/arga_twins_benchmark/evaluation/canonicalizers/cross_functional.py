from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from arga_twins_benchmark.evaluation.deterministic import CanonicalResource
from arga_twins_benchmark.evaluation.state_capture import CapturedQueryState, StateCaptureError

_IDENTITY_FIELDS = (
    "id",
    "Id",
    "key",
    "identifier",
    "number",
    "email",
    "emailAddress",
    "name",
    "Name",
    "ts",
    "uuid",
)
_NESTED_ENTITY_COLLECTIONS = frozenset(
    {
        "accounts",
        "associations",
        "blocks",
        "calendars",
        "channels",
        "comments",
        "companies",
        "contacts",
        "customers",
        "databases",
        "deals",
        "drafts",
        "events",
        "files",
        "issues",
        "leads",
        "messages",
        "meters",
        "notes",
        "opportunities",
        "pages",
        "posts",
        "prices",
        "projects",
        "prs",
        "pull_requests",
        "records",
        "repos",
        "repositories",
        "reviews",
        "tasks",
        "teams",
        "tickets",
        "ugc_posts",
        "users",
    }
)
_COMMON_OPERATIONAL_KEYS = frozenset(
    {
        "access_tokens",
        "authorization_codes",
        "base_time",
        "clock",
        "counts",
        "deliveries",
        "failure_injection",
        "failure_rules",
        "generic_hits",
        "id_tokens",
        "idempotency",
        "jobs",
        "logical_now",
        "meta",
        "notifications",
        "oauth",
        "rate_buckets",
        "rate_limiting_enabled",
        "replays",
        "request_log",
        "schema_version",
        "seed",
        "seed_config",
        "subscriptions",
        "sync_id",
        "webhooks",
    }
)
_PROVIDER_OPERATIONAL_KEYS: dict[str, frozenset[str]] = {
    "gmail": frozenset({"history", "settings", "watches"}),
    "github": frozenset({"_links", "events", "installations", "oauth_codes"}),
    "google_drive": frozenset({"changes", "channels", "events"}),
    "hubspot": frozenset({"events", "generic_resources", "generic_singletons"}),
    "linkedin": frozenset({"applications", "config", "counters", "events"}),
    "notion": frozenset({"events", "file_uploads", "views"}),
    "salesforce": frozenset({"configured_apps", "jobs", "now", "ok", "service"}),
    "slack": frozenset({"apps", "events", "legacy_preferences", "legacy_resources", "triggers"}),
    "stripe": frozenset({"events", "generic_resources", "generic_singletons", "meter_event_identifiers"}),
}


def _error(capture: CapturedQueryState, message: str) -> StateCaptureError:
    return StateCaptureError(f"canonicalizer {capture.canonicalizer!r} for query {capture.query_id!r}: {message}")


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _slug(value: str) -> str:
    rendered = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return rendered or "resource"


def _singular(value: str) -> str:
    normalized = _slug(value)
    if normalized.endswith("ies") and len(normalized) > 3:
        return f"{normalized[:-3]}y"
    if normalized.endswith("sses"):
        return normalized[:-2]
    if normalized.endswith("s") and not normalized.endswith("ss"):
        return normalized[:-1]
    return normalized


def _skip_key(provider: str, key: str) -> bool:
    return key in _COMMON_OPERATIONAL_KEYS or key in _PROVIDER_OPERATIONAL_KEYS.get(provider, frozenset())


def _stable_value(value: object, *, provider: str) -> object:
    if isinstance(value, dict):
        return {
            str(key): _stable_value(item, provider=provider)
            for key, item in sorted(cast(dict[object, object], value).items(), key=lambda pair: str(pair[0]))
            if isinstance(key, str) and not _skip_key(provider, key)
        }
    if isinstance(value, list):
        return [_stable_value(item, provider=provider) for item in cast(list[object], value)]
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def _identity(record: Mapping[str, Any], fallback: str) -> str:
    for field in _IDENTITY_FIELDS:
        value = record.get(field)
        if isinstance(value, str | int) and not isinstance(value, bool) and str(value):
            return str(value)
    return fallback


def _explicit_identity(record: Mapping[str, Any]) -> str | None:
    for field in _IDENTITY_FIELDS:
        value = record.get(field)
        if isinstance(value, str | int) and not isinstance(value, bool) and str(value):
            return str(value)
    return None


def _has_business_content(record: Mapping[str, Any]) -> bool:
    return any(
        key not in {"created_at", "updated_at", "createdAt", "updatedAt"}
        and (value is not None and value != "" and not isinstance(value, dict | list))
        for key, value in record.items()
    )


def _walk_entities(
    value: object,
    *,
    capture: CapturedQueryState,
    provider: str,
    path: tuple[str, ...],
    ancestor_context: tuple[str, ...],
) -> list[CanonicalResource]:
    resources: list[CanonicalResource] = []
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        stable = cast(dict[str, Any], _stable_value(mapping, provider=provider))
        explicit_identity = _explicit_identity(stable)
        identity_context = tuple(
            str(item)
            for field, item in stable.items()
            if field not in {"created_at", "updated_at", "createdAt", "updatedAt"}
            and isinstance(item, str | int | float)
            and not isinstance(item, bool)
            and len(str(item)) <= 1000
        )
        next_context = (*ancestor_context, *identity_context)
        is_entity = bool(path and explicit_identity is not None and _has_business_content(stable))
        if is_entity:
            fallback = path[-1]
            identity = explicit_identity or fallback
            collection = path[-2] if len(path) > 1 else fallback
            resource_type = _singular(collection)
            resource_id = f"{provider}:{'/'.join(path[:-1])}:{identity}"
            fields = {
                "provider": provider,
                "collection": "/".join(path[:-1]),
                "ancestor_context": list(dict.fromkeys(ancestor_context)),
                "semantic_text": _stable_json(stable),
                **stable,
            }
            resources.append(CanonicalResource(capture.provider_role, resource_type, resource_id, fields))
        for key, item in mapping.items():
            if not _skip_key(provider, key) and (not is_entity or _slug(key) in _NESTED_ENTITY_COLLECTIONS):
                resources.extend(
                    _walk_entities(
                        item,
                        capture=capture,
                        provider=provider,
                        path=(*path, str(key)),
                        ancestor_context=next_context,
                    )
                )
    elif isinstance(value, list):
        items = cast(list[object], value)
        for index, item in enumerate(items):
            fallback = str(index)
            next_value: object = item
            if isinstance(item, dict):
                typed_item = cast(dict[str, Any], item)
                fallback = _identity(typed_item, fallback)
                next_value = typed_item
            resources.extend(
                _walk_entities(
                    next_value,
                    capture=capture,
                    provider=provider,
                    path=(*path, fallback),
                    ancestor_context=ancestor_context,
                )
            )
    return resources


def cross_functional_admin_state_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    """Project a trusted full-state read into stable, route-independent entities.

    The projection intentionally drops operational clocks, request logs, delivery
    queues, credentials, failure controls, and seed echoes.  Resource identities
    derive from provider IDs (or their identity-keyed collection key), so the same
    business object is compared before and after regardless of the API route used
    to mutate it.
    """

    if not isinstance(capture.body, dict):
        raise _error(capture, "admin state must be a JSON object")
    provider = capture.provider_name
    resources = _walk_entities(
        cast(dict[str, Any], capture.body),
        capture=capture,
        provider=provider,
        path=(),
        ancestor_context=(),
    )
    unique: dict[tuple[str, str], CanonicalResource] = {}
    for resource in resources:
        key = (resource.resource_type, resource.resource_id)
        existing = unique.get(key)
        if existing is not None and existing.fields != resource.fields:
            raise _error(capture, f"conflicting projection for {resource.resource_id!r}")
        unique[key] = resource
    if not unique:
        empty = CanonicalResource(
            capture.provider_role,
            "query_result",
            f"{provider}:{capture.query_id}:empty",
            {"provider": provider, "query_id": capture.query_id, "empty": True},
        )
        return [empty]
    return [unique[key] for key in sorted(unique)]


CROSS_FUNCTIONAL_CANONICALIZERS = {
    "cross_functional_admin_state_v1": cross_functional_admin_state_v1,
}


__all__ = ["CROSS_FUNCTIONAL_CANONICALIZERS", "cross_functional_admin_state_v1"]
