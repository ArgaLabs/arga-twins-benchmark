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
    "slack": frozenset(
        {"apps", "events", "legacy_preferences", "legacy_resources", "message_count", "triggers"}
    ),
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
        entity_stable = {
            key: item for key, item in stable.items() if _slug(key) not in _NESTED_ENTITY_COLLECTIONS
        }
        explicit_identity = _explicit_identity(entity_stable)
        is_entity = bool(path and explicit_identity is not None and _has_business_content(entity_stable))
        context_values = tuple(
            str(entity_stable[field])
            for field in ("id", "key", "identifier", "name", "Name", "email", "emailAddress")
            if field in entity_stable
            and isinstance(entity_stable[field], str | int)
            and not isinstance(entity_stable[field], bool)
        )
        next_context = (*ancestor_context, *dict.fromkeys(context_values)) if is_entity else ancestor_context
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
                "semantic_text": _stable_json(entity_stable),
                **entity_stable,
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


def _slack_event_messages(capture: CapturedQueryState) -> list[CanonicalResource]:
    if capture.provider_name != "slack" or not isinstance(capture.body, dict):
        return []
    body = cast(dict[str, Any], capture.body)
    channels = body.get("channels")
    channel_names: dict[str, str] = {}
    if isinstance(channels, list):
        for raw_channel in cast(list[object], channels):
            if not isinstance(raw_channel, dict):
                continue
            channel = cast(dict[str, Any], raw_channel)
            channel_id = channel.get("id")
            channel_name = channel.get("name")
            if isinstance(channel_id, str) and isinstance(channel_name, str):
                channel_names[channel_id] = channel_name

    raw_events = body.get("events")
    if not isinstance(raw_events, list):
        return []
    messages: list[CanonicalResource] = []
    for index, raw_record in enumerate(cast(list[object], raw_events)):
        if not isinstance(raw_record, dict):
            continue
        record = cast(dict[str, Any], raw_record)
        envelope = record.get("envelope")
        if not isinstance(envelope, dict):
            continue
        typed_envelope = cast(dict[str, Any], envelope)
        event = typed_envelope.get("event")
        if not isinstance(event, dict):
            continue
        typed_event = cast(dict[str, Any], event)
        if typed_event.get("type") != "message" or not isinstance(typed_event.get("text"), str):
            continue
        channel = str(typed_event.get("channel", ""))
        identity = str(
            record.get("id") or typed_envelope.get("event_id") or typed_event.get("ts") or index
        )
        stable = {
            "provider": "slack",
            "channel": channel,
            "channel_name": channel_names.get(channel, ""),
            "text": typed_event["text"],
            "ts": typed_event.get("ts"),
            "user": typed_event.get("user"),
            "source_method": record.get("source_method"),
        }
        messages.append(
            CanonicalResource(
                capture.provider_role,
                "message",
                f"slack:messages:{identity}",
                {"semantic_text": _stable_json(stable), **stable},
            )
        )
    return messages


def argabench_admin_state_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
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
    resources = [
        *_slack_event_messages(capture),
        *_walk_entities(
            cast(dict[str, Any], capture.body),
            capture=capture,
            provider=provider,
            path=(),
            ancestor_context=(),
        ),
    ]
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


ARGABENCH_CANONICALIZERS = {
    "argabench_admin_state_v1": argabench_admin_state_v1,
}


__all__ = ["ARGABENCH_CANONICALIZERS", "argabench_admin_state_v1"]
