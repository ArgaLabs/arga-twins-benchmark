from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import SplitResult, quote, urlsplit

import httpx

from arga_twins_benchmark.evaluation.deterministic import CanonicalResource
from arga_twins_benchmark.evaluation.protocol import JsonValue, Mutation
from arga_twins_benchmark.providers import provider_request_headers
from arga_twins_benchmark.specs.models import SnapshotQuerySpec

ADMIN_STATE_PATH = "/admin/state"
CONTROL_PROTOCOL = "arga-bench-control/1"
_ADMIN_STATE_PATHS: dict[str, tuple[str, ...]] = {
    # The current Calendar twin uses the underscored namespace. The legacy
    # alias remains as a fallback for scenarios pinned to older images.
    "google_calendar": ("/_admin/state", "/admin/state"),
    # Current Gmail exposes /inspect; pinned benchmark images also expose the
    # common /admin/state alias.
    "gmail": ("/admin/state", "/inspect"),
}
_TRUSTED_CONTROL_READ_PATHS = frozenset({"/admin/state", "/_admin/state", "/inspect"})
_CONTROL_FIRST_SEGMENTS = frozenset({"admin", "_admin", "_twin", "inspect", "reset"})
_LINEAR_SNAPSHOT_QUERY = """\
query ArgaBenchmarkSnapshot {
  issues(first: 250) {
    nodes {
      id
      identifier
      number
      title
      description
      priority
      createdAt
      updatedAt
      archivedAt
      completedAt
      canceledAt
      team { id key name }
      state { id name type }
      project { id name state }
      comments {
        nodes {
          id
          body
          createdAt
          updatedAt
          user { id name email }
        }
      }
    }
  }
  projects(first: 250) {
    nodes {
      id
      name
      slugId
      state
      description
      summary
      createdAt
      updatedAt
      archivedAt
    }
  }
}
"""
_LINEAR_CANONICALIZERS = frozenset(
    {
        "linear_issues_comments_v1",
        "linear_issues_projects_comments_v1",
        "linear_team_ENG_issues_stable",
        "linear_team_OPS_issues_stable",
        "linear_team_REL_issues_comments_stable",
    }
)
_STABLE_ID_FIELDS = (
    "id",
    "resource_id",
    "key",
    "identifier",
    "number",
    "iid",
    "ts",
    "emailAddress",
    "email",
    "name",
)
_RETRYABLE_STATE_CAPTURE_STATUS_CODES = frozenset({429, 502, 503, 504})


class StateCaptureError(RuntimeError):
    """Raised when trusted state cannot be captured or compared safely."""


class _StateCaptureHttpError(StateCaptureError):
    def __init__(self, label: str, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"{label} returned HTTP {status_code}")


@dataclass(frozen=True)
class TrustedProviderTarget:
    """A verifier-only provider target derived from an Arga CLI control payload.

    This object deliberately has no candidate serialization method. Its admin URL,
    proxy cookie, and raw environment values must remain on the trusted side of the
    benchmark runner.
    """

    provider_name: str
    provider_role: str
    base_url: str = field(repr=False)
    admin_url: str = field(repr=False)
    env: Mapping[str, str] = field(default_factory=lambda: dict[str, str](), repr=False, compare=False)


@dataclass(frozen=True)
class CapturedProviderState:
    provider_name: str
    provider_role: str
    state: dict[str, JsonValue]


@dataclass(frozen=True)
class CapturedQueryState:
    query_id: str
    provider_name: str
    provider_role: str
    method: str
    path: str
    canonicalizer: str
    status_code: int
    body: JsonValue


@dataclass(frozen=True)
class TrustedStateSnapshot:
    """Raw trusted evidence without control-plane addresses or credentials."""

    providers: dict[str, CapturedProviderState]
    queries: dict[str, CapturedQueryState] = field(default_factory=lambda: dict[str, CapturedQueryState]())

    def artifact_payload(self) -> dict[str, JsonValue]:
        """Return a secret-safe payload suitable for a private trial artifact."""

        return {
            "providers": {
                provider_name: {
                    "provider_role": capture.provider_role,
                    "state": capture.state,
                }
                for provider_name, capture in sorted(self.providers.items())
            },
            "queries": {
                query_id: {
                    "provider_name": capture.provider_name,
                    "provider_role": capture.provider_role,
                    "method": capture.method,
                    "path": capture.path,
                    "canonicalizer": capture.canonicalizer,
                    "status_code": capture.status_code,
                    "body": capture.body,
                }
                for query_id, capture in sorted(self.queries.items())
            },
        }

    @classmethod
    def from_artifact_payload(cls, payload: object) -> TrustedStateSnapshot:
        """Reconstruct one trusted snapshot from its secret-safe artifact.

        Offline grading must not silently accept a partial or structurally
        ambiguous snapshot. The loader therefore validates every relationship
        that the live capturer establishes before returning an equivalent
        in-memory object.
        """

        if not isinstance(payload, dict):
            raise StateCaptureError("trusted state artifact must be a JSON object")
        artifact = cast(dict[object, object], payload)
        if set(artifact) != {"providers", "queries"}:
            raise StateCaptureError("trusted state artifact must contain exactly providers and queries")

        raw_providers = artifact["providers"]
        if not isinstance(raw_providers, dict) or not raw_providers:
            raise StateCaptureError("trusted state artifact providers must be a non-empty JSON object")
        providers: dict[str, CapturedProviderState] = {}
        for raw_name, raw_capture in cast(dict[object, object], raw_providers).items():
            if not isinstance(raw_name, str) or not raw_name:
                raise StateCaptureError("trusted state artifact provider names must be non-empty strings")
            if not isinstance(raw_capture, dict):
                raise StateCaptureError(f"trusted state artifact provider {raw_name!r} must be a JSON object")
            capture = cast(dict[object, object], raw_capture)
            if set(capture) != {"provider_role", "state"}:
                raise StateCaptureError(
                    f"trusted state artifact provider {raw_name!r} must contain exactly provider_role and state"
                )
            provider_role = capture["provider_role"]
            state = capture["state"]
            if not isinstance(provider_role, str) or not provider_role:
                raise StateCaptureError(f"trusted state artifact provider {raw_name!r} has an invalid provider_role")
            if not isinstance(state, dict):
                raise StateCaptureError(f"trusted state artifact provider {raw_name!r} state must be a JSON object")
            state_mapping = cast(dict[object, object], state)
            if not all(isinstance(key, str) for key in state_mapping):
                raise StateCaptureError(f"trusted state artifact provider {raw_name!r} state must use string keys")
            providers[raw_name] = CapturedProviderState(
                provider_name=raw_name,
                provider_role=provider_role,
                state=cast(dict[str, JsonValue], state_mapping),
            )

        raw_queries = artifact["queries"]
        if not isinstance(raw_queries, dict):
            raise StateCaptureError("trusted state artifact queries must be a JSON object")
        queries: dict[str, CapturedQueryState] = {}
        required_query_fields = {
            "provider_name",
            "provider_role",
            "method",
            "path",
            "canonicalizer",
            "status_code",
            "body",
        }
        for raw_query_id, raw_capture in cast(dict[object, object], raw_queries).items():
            if not isinstance(raw_query_id, str) or not raw_query_id:
                raise StateCaptureError("trusted state artifact query IDs must be non-empty strings")
            if not isinstance(raw_capture, dict):
                raise StateCaptureError(f"trusted state artifact query {raw_query_id!r} must be a JSON object")
            capture = cast(dict[object, object], raw_capture)
            if set(capture) != required_query_fields:
                raise StateCaptureError(f"trusted state artifact query {raw_query_id!r} has an invalid field set")
            provider_name = capture["provider_name"]
            provider_role = capture["provider_role"]
            method = capture["method"]
            path = capture["path"]
            canonicalizer = capture["canonicalizer"]
            status_code = capture["status_code"]
            if not isinstance(provider_name, str) or provider_name not in providers:
                raise StateCaptureError(f"trusted state artifact query {raw_query_id!r} references an unknown provider")
            if not isinstance(provider_role, str) or provider_role != providers[provider_name].provider_role:
                raise StateCaptureError(f"trusted state artifact query {raw_query_id!r} has a mismatched provider role")
            if not isinstance(method, str) or not method:
                raise StateCaptureError(f"trusted state artifact query {raw_query_id!r} has an invalid method")
            if not isinstance(path, str) or not path:
                raise StateCaptureError(f"trusted state artifact query {raw_query_id!r} has an invalid path")
            if not isinstance(canonicalizer, str) or not canonicalizer:
                raise StateCaptureError(f"trusted state artifact query {raw_query_id!r} has an invalid canonicalizer")
            if isinstance(status_code, bool) or not isinstance(status_code, int):
                raise StateCaptureError(f"trusted state artifact query {raw_query_id!r} has an invalid status_code")
            queries[raw_query_id] = CapturedQueryState(
                query_id=raw_query_id,
                provider_name=provider_name,
                provider_role=provider_role,
                method=method,
                path=path,
                canonicalizer=canonicalizer,
                status_code=status_code,
                body=cast(JsonValue, capture["body"]),
            )
        return cls(providers=providers, queries=queries)


@dataclass(frozen=True)
class RawStateDelta:
    provider_name: str
    provider_role: str
    scope: str
    path: tuple[str, ...]
    operation: str
    before: JsonValue = None
    after: JsonValue = None

    @property
    def json_pointer(self) -> str:
        escaped = (part.replace("~", "~0").replace("/", "~1") for part in self.path)
        return "/" + "/".join(escaped)


type SnapshotCanonicalizer = Callable[[CapturedQueryState], Sequence[CanonicalResource]]


def targets_from_control(
    control_payload: Mapping[str, Any],
    *,
    roles: Mapping[str, str],
) -> dict[str, TrustedProviderTarget]:
    """Resolve verifier-only URLs from Arga CLI JSON.

    ``roles`` maps benchmark provider roles to concrete twin names, for example
    ``{"code_host": "github"}``. Every provisioned twin must have one role and a
    distinct admin URL; ambiguity is rejected instead of silently weakening the
    verifier.
    """

    if control_payload.get("protocol") != CONTROL_PROTOCOL:
        raise StateCaptureError(f"control payload must use protocol {CONTROL_PROTOCOL!r}")
    raw_twin_run = control_payload.get("twin_run")
    if not isinstance(raw_twin_run, dict):
        raise StateCaptureError("control payload is missing twin_run")
    twin_run = cast(dict[str, Any], raw_twin_run)
    raw_twins = twin_run.get("twins")
    if not isinstance(raw_twins, dict) or not raw_twins:
        raise StateCaptureError("control payload is missing provisioned twins")

    provider_to_role: dict[str, str] = {}
    for role, provider_name in roles.items():
        if not role or not provider_name:
            raise StateCaptureError("binding roles and provider names must be non-empty")
        if provider_name in provider_to_role:
            raise StateCaptureError(f"provider {provider_name!r} is assigned to more than one role")
        provider_to_role[provider_name] = role

    targets: dict[str, TrustedProviderTarget] = {}
    for provider_name, raw_target in cast(dict[object, object], raw_twins).items():
        if not isinstance(provider_name, str) or not isinstance(raw_target, dict):
            raise StateCaptureError("twin_run.twins must map provider names to objects")
        role = provider_to_role.get(provider_name)
        if role is None:
            raise StateCaptureError(f"provisioned provider {provider_name!r} is absent from the binding")
        raw = cast(dict[str, Any], raw_target)
        base_url = _validated_base_url(raw.get("base_url"), f"{provider_name} base_url")
        admin_url = _validated_base_url(raw.get("admin_url"), f"{provider_name} admin_url")
        if base_url == admin_url:
            raise StateCaptureError(f"provider {provider_name!r} must have a separate verifier admin URL")
        raw_env = raw.get("env_vars", {})
        if not isinstance(raw_env, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in cast(dict[object, object], raw_env).items()
        ):
            raise StateCaptureError(f"provider {provider_name!r} env_vars must contain only strings")
        targets[provider_name] = TrustedProviderTarget(
            provider_name=provider_name,
            provider_role=role,
            base_url=base_url,
            admin_url=admin_url,
            env=cast(dict[str, str], raw_env),
        )

    missing = sorted(set(provider_to_role) - set(targets))
    if missing:
        raise StateCaptureError(f"binding providers were not provisioned: {', '.join(missing)}")
    return targets


class TrustedStateCapturer:
    """Capture whole-twin admin state plus deterministic verifier read queries."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
        retry_base_delay_seconds: float = 0.5,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retry_base_delay_seconds < 0:
            raise ValueError("retry_base_delay_seconds must be non-negative")
        self._client = client
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._retry_base_delay_seconds = retry_base_delay_seconds

    async def capture(
        self,
        control_payload: Mapping[str, Any],
        *,
        roles: Mapping[str, str],
        snapshot_queries: Sequence[SnapshotQuerySpec] = (),
    ) -> TrustedStateSnapshot:
        targets = targets_from_control(control_payload, roles=roles)
        proxy_token = _proxy_token(control_payload)
        role_targets = {target.provider_role: target for target in targets.values()}
        query_ids: set[str] = set()
        for query in snapshot_queries:
            if query.id in query_ids:
                raise StateCaptureError(f"duplicate snapshot query id {query.id!r}")
            query_ids.add(query.id)
            if query.provider_role not in role_targets:
                raise StateCaptureError(f"snapshot query {query.id!r} uses unknown role {query.provider_role!r}")
            snapshot_query_uses_control_plane(query)

        if self._client is not None:
            return await self._capture_with_client(
                self._client,
                targets=targets,
                role_targets=role_targets,
                proxy_token=proxy_token,
                snapshot_queries=snapshot_queries,
            )
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
            return await self._capture_with_client(
                client,
                targets=targets,
                role_targets=role_targets,
                proxy_token=proxy_token,
                snapshot_queries=snapshot_queries,
            )

    async def _capture_with_client(
        self,
        client: httpx.AsyncClient,
        *,
        targets: Mapping[str, TrustedProviderTarget],
        role_targets: Mapping[str, TrustedProviderTarget],
        proxy_token: str | None,
        snapshot_queries: Sequence[SnapshotQuerySpec],
    ) -> TrustedStateSnapshot:
        provider_captures: dict[str, CapturedProviderState] = {}
        for provider_name, target in sorted(targets.items()):
            state = await _capture_admin_state(
                client,
                target=target,
                proxy_token=proxy_token,
                max_attempts=self._max_attempts,
                retry_base_delay_seconds=self._retry_base_delay_seconds,
            )
            if not isinstance(state, dict):
                raise StateCaptureError(f"{provider_name} admin state must be a JSON object")
            provider_captures[provider_name] = CapturedProviderState(
                provider_name=provider_name,
                provider_role=target.provider_role,
                state=state,
            )

        query_captures: dict[str, CapturedQueryState] = {}
        for query in snapshot_queries:
            target = role_targets[query.provider_role]
            use_control_plane = snapshot_query_uses_control_plane(query)
            base_url = target.admin_url if use_control_plane else target.base_url
            response = await _request_json_response(
                client,
                method=query.method,
                url=_joined_url(base_url, query.path),
                headers=_verifier_headers(target),
                proxy_token=proxy_token,
                label=f"snapshot query {query.id}",
                json_body=snapshot_query_request_body(query),
                max_attempts=self._max_attempts,
                retry_base_delay_seconds=self._retry_base_delay_seconds,
            )
            query_captures[query.id] = CapturedQueryState(
                query_id=query.id,
                provider_name=target.provider_name,
                provider_role=query.provider_role,
                method=query.method,
                path=query.path,
                canonicalizer=query.canonicalizer,
                status_code=response.status_code,
                body=response.body,
            )

        return TrustedStateSnapshot(providers=provider_captures, queries=query_captures)


@dataclass(frozen=True)
class _JsonResponse:
    status_code: int
    body: JsonValue


async def _capture_admin_state(
    client: httpx.AsyncClient,
    *,
    target: TrustedProviderTarget,
    proxy_token: str | None,
    max_attempts: int,
    retry_base_delay_seconds: float,
) -> JsonValue:
    paths = _ADMIN_STATE_PATHS.get(target.provider_name, (ADMIN_STATE_PATH,))
    for index, path in enumerate(paths):
        try:
            return await _request_json(
                client,
                method="GET",
                url=_joined_url(target.admin_url, path),
                headers=_verifier_headers(target),
                proxy_token=proxy_token,
                label=f"{target.provider_name} admin state",
                max_attempts=max_attempts,
                retry_base_delay_seconds=retry_base_delay_seconds,
            )
        except _StateCaptureHttpError as error:
            if error.status_code not in {404, 410} or index == len(paths) - 1:
                raise
    raise AssertionError("admin state path iteration must return or raise")


async def _request_json(
    client: httpx.AsyncClient,
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    proxy_token: str | None,
    label: str,
    max_attempts: int,
    retry_base_delay_seconds: float,
) -> JsonValue:
    return (
        await _request_json_response(
            client,
            method=method,
            url=url,
            headers=headers,
            proxy_token=proxy_token,
            label=label,
            max_attempts=max_attempts,
            retry_base_delay_seconds=retry_base_delay_seconds,
        )
    ).body


async def _request_json_response(
    client: httpx.AsyncClient,
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    proxy_token: str | None,
    label: str,
    max_attempts: int,
    retry_base_delay_seconds: float,
    json_body: dict[str, JsonValue] | None = None,
) -> _JsonResponse:
    request_headers = dict(headers)
    if proxy_token:
        request_headers["Cookie"] = f"arga_env_proxy_token={quote(proxy_token, safe='')}"
    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.request(
                method,
                url,
                headers=request_headers,
                json=json_body,
            )
        except httpx.TransportError as error:
            if attempt == max_attempts:
                raise StateCaptureError(
                    f"{label} request failed after {max_attempts} attempts: {type(error).__name__}"
                ) from error
            await asyncio.sleep(retry_base_delay_seconds * (2 ** (attempt - 1)))
            continue
        if response.status_code in _RETRYABLE_STATE_CAPTURE_STATUS_CODES and attempt < max_attempts:
            await response.aclose()
            await asyncio.sleep(retry_base_delay_seconds * (2 ** (attempt - 1)))
            continue
        if not 200 <= response.status_code <= 299:
            raise _StateCaptureHttpError(label, response.status_code)
        break
    else:
        raise AssertionError("state capture retry loop must return or raise")
    try:
        raw_body = response.json()
    except json.JSONDecodeError as error:
        raise StateCaptureError(f"{label} did not return JSON") from error
    return _JsonResponse(status_code=response.status_code, body=_json_value(raw_body, label=label))


def normalize_json(value: JsonValue) -> JsonValue:
    """Normalize mappings and identity-keyed arrays for deterministic comparison."""

    if isinstance(value, dict):
        return {key: normalize_json(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        normalized = [normalize_json(item) for item in value]
        identities = [_stable_identity(item) for item in normalized]
        if (
            normalized
            and all(identity is not None for identity in identities)
            and len(set(identities)) == len(identities)
        ):
            paired = list(zip(cast(list[str], identities), normalized, strict=True))
            return [item for _, item in sorted(paired, key=lambda pair: pair[0])]
        return normalized
    return value


def diff_trusted_states(
    before: TrustedStateSnapshot,
    after: TrustedStateSnapshot,
) -> list[RawStateDelta]:
    """Return deterministic raw evidence deltas without claiming semantic types."""

    if set(before.providers) != set(after.providers):
        raise StateCaptureError("before and after captures contain different providers")
    if set(before.queries) != set(after.queries):
        raise StateCaptureError("before and after captures contain different snapshot queries")

    deltas: list[RawStateDelta] = []
    for provider_name in sorted(before.providers):
        old = before.providers[provider_name]
        new = after.providers[provider_name]
        if old.provider_role != new.provider_role:
            raise StateCaptureError(f"provider role changed for {provider_name!r}")
        deltas.extend(
            _diff_json(
                normalize_json(old.state),
                normalize_json(new.state),
                provider_name=provider_name,
                provider_role=old.provider_role,
                scope="admin",
            )
        )

    for query_id in sorted(before.queries):
        old_query = before.queries[query_id]
        new_query = after.queries[query_id]
        old_contract = (
            old_query.provider_name,
            old_query.provider_role,
            old_query.method,
            old_query.path,
            old_query.canonicalizer,
        )
        new_contract = (
            new_query.provider_name,
            new_query.provider_role,
            new_query.method,
            new_query.path,
            new_query.canonicalizer,
        )
        if old_contract != new_contract:
            raise StateCaptureError(f"snapshot query contract changed for {query_id!r}")
        deltas.extend(
            _diff_json(
                normalize_json(old_query.body),
                normalize_json(new_query.body),
                provider_name=old_query.provider_name,
                provider_role=old_query.provider_role,
                scope=f"query:{query_id}",
            )
        )
    return deltas


def canonicalize_query_results(
    snapshot: TrustedStateSnapshot,
    *,
    canonicalizers: Mapping[str, SnapshotCanonicalizer],
) -> list[CanonicalResource]:
    """Apply explicit provider canonicalizers and merge compatible projections.

    There is intentionally no fallback canonicalizer. A raw twin snapshot is
    sufficient to prove that something changed, but it cannot safely infer
    benchmark concepts such as ``pull_request_review`` versus ``issue_comment``.
    Missing adapters therefore fail closed.
    """

    merged: dict[tuple[str, str, str], CanonicalResource] = {}
    for query_id, capture in sorted(snapshot.queries.items()):
        canonicalizer = canonicalizers.get(capture.canonicalizer)
        if canonicalizer is None:
            raise StateCaptureError(
                f"snapshot query {query_id!r} references unregistered canonicalizer {capture.canonicalizer!r}"
            )
        for resource in canonicalizer(capture):
            if resource.provider_role != capture.provider_role:
                raise StateCaptureError(
                    f"canonicalizer {capture.canonicalizer!r} returned role {resource.provider_role!r} "
                    f"for query role {capture.provider_role!r}"
                )
            key = (resource.provider_role, resource.resource_type, resource.resource_id)
            existing = merged.get(key)
            if existing is None:
                merged[key] = resource
                continue
            combined_fields = dict(existing.fields)
            for field_name, value in resource.fields.items():
                if field_name in combined_fields and combined_fields[field_name] != value:
                    raise StateCaptureError(
                        f"conflicting canonical field {field_name!r} for "
                        f"{resource.provider_role}/{resource.resource_type}/{resource.resource_id}"
                    )
                combined_fields[field_name] = value
            merged[key] = CanonicalResource(
                provider_role=resource.provider_role,
                resource_type=resource.resource_type,
                resource_id=resource.resource_id,
                fields=combined_fields,
            )
    return [merged[key] for key in sorted(merged)]


def diff_canonical_resources(
    before: Sequence[CanonicalResource],
    after: Sequence[CanonicalResource],
) -> list[Mutation]:
    """Convert complete canonical resource projections into semantic mutations."""

    old = _resource_index(before, label="before")
    new = _resource_index(after, label="after")
    mutations: list[Mutation] = []
    for key in sorted(set(old) | set(new)):
        provider_role, resource_type, resource_id = key
        old_resource = old.get(key)
        new_resource = new.get(key)
        if old_resource is None and new_resource is not None:
            mutations.append(
                Mutation(
                    twin=provider_role,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    operation="create",
                    after=_json_value(new_resource.fields, label="canonical resource fields"),
                )
            )
        elif old_resource is not None and new_resource is None:
            mutations.append(
                Mutation(
                    twin=provider_role,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    operation="delete",
                    before=_json_value(old_resource.fields, label="canonical resource fields"),
                )
            )
        elif old_resource is not None and new_resource is not None and old_resource.fields != new_resource.fields:
            mutations.append(
                Mutation(
                    twin=provider_role,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    operation="update",
                    before=_json_value(old_resource.fields, label="canonical resource fields"),
                    after=_json_value(new_resource.fields, label="canonical resource fields"),
                )
            )
    return mutations


def snapshot_query_uses_control_plane(query: SnapshotQuerySpec) -> bool:
    """Validate a manifest query and decide which verifier-only origin to use.

    Only exact, read-only state endpoints are allowed on the control plane.
    This is intentionally narrower than the candidate gateway's blanket
    control-path denial and cannot be used to seed, reset, or mutate a twin.
    """

    parsed = _validated_relative_path(query.path, label=f"snapshot query {query.id}")
    if parsed.path in _TRUSTED_CONTROL_READ_PATHS:
        if query.method != "GET":
            raise StateCaptureError(f"snapshot query {query.id!r} control-plane reads must use GET")
        return True
    first_segment = next((segment for segment in parsed.path.split("/") if segment), "")
    if first_segment in _CONTROL_FIRST_SEGMENTS:
        raise StateCaptureError(f"snapshot query {query.id!r} may not target control-plane route {parsed.path!r}")
    return False


def snapshot_query_request_body(query: SnapshotQuerySpec) -> dict[str, JsonValue] | None:
    """Return the deterministic body for a manifest-declared snapshot read."""

    if query.method == "GET":
        return None
    if query.path == "/graphql" and query.canonicalizer in _LINEAR_CANONICALIZERS:
        return {"query": _LINEAR_SNAPSHOT_QUERY, "operationName": "ArgaBenchmarkSnapshot"}
    # Slack conversations.list and Notion search both use an empty read body.
    # Unknown POST readers retain that provider-native empty request rather
    # than guessing task data that is absent from the manifest schema.
    return {}


def _resource_index(
    resources: Sequence[CanonicalResource],
    *,
    label: str,
) -> dict[tuple[str, str, str], CanonicalResource]:
    indexed: dict[tuple[str, str, str], CanonicalResource] = {}
    for resource in resources:
        key = (resource.provider_role, resource.resource_type, resource.resource_id)
        if key in indexed:
            raise StateCaptureError(f"{label} canonical resources contain duplicate identity {key!r}")
        indexed[key] = resource
    return indexed


def _diff_json(
    before: JsonValue,
    after: JsonValue,
    *,
    provider_name: str,
    provider_role: str,
    scope: str,
    path: tuple[str, ...] = (),
) -> list[RawStateDelta]:
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        deltas: list[RawStateDelta] = []
        for key in sorted(set(before) | set(after)):
            next_path = (*path, key)
            if key not in before:
                deltas.append(
                    RawStateDelta(
                        provider_name,
                        provider_role,
                        scope,
                        next_path,
                        "create",
                        after=after[key],
                    )
                )
            elif key not in after:
                deltas.append(
                    RawStateDelta(
                        provider_name,
                        provider_role,
                        scope,
                        next_path,
                        "delete",
                        before=before[key],
                    )
                )
            else:
                deltas.extend(
                    _diff_json(
                        before[key],
                        after[key],
                        provider_name=provider_name,
                        provider_role=provider_role,
                        scope=scope,
                        path=next_path,
                    )
                )
        return deltas
    if isinstance(before, list) and isinstance(after, list):
        old_items = _identity_index(before)
        new_items = _identity_index(after)
        if old_items is not None and new_items is not None:
            deltas = []
            for identity in sorted(set(old_items) | set(new_items)):
                next_path = (*path, identity)
                if identity not in old_items:
                    deltas.append(
                        RawStateDelta(
                            provider_name,
                            provider_role,
                            scope,
                            next_path,
                            "create",
                            after=new_items[identity],
                        )
                    )
                elif identity not in new_items:
                    deltas.append(
                        RawStateDelta(
                            provider_name,
                            provider_role,
                            scope,
                            next_path,
                            "delete",
                            before=old_items[identity],
                        )
                    )
                else:
                    deltas.extend(
                        _diff_json(
                            old_items[identity],
                            new_items[identity],
                            provider_name=provider_name,
                            provider_role=provider_role,
                            scope=scope,
                            path=next_path,
                        )
                    )
            return deltas
    return [
        RawStateDelta(
            provider_name=provider_name,
            provider_role=provider_role,
            scope=scope,
            path=path,
            operation="update",
            before=before,
            after=after,
        )
    ]


def _identity_index(items: list[JsonValue]) -> dict[str, JsonValue] | None:
    if not items:
        return {}
    identities = [_stable_identity(item) for item in items]
    if any(identity is None for identity in identities):
        return None
    typed_identities = cast(list[str], identities)
    if len(set(typed_identities)) != len(typed_identities):
        return None
    return dict(zip(typed_identities, items, strict=True))


def _stable_identity(value: JsonValue) -> str | None:
    if not isinstance(value, dict):
        return None
    for field_name in _STABLE_ID_FIELDS:
        candidate = value.get(field_name)
        if isinstance(candidate, str | int) and str(candidate):
            return f"{field_name}={candidate}"
    return None


def _validated_base_url(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise StateCaptureError(f"{label} is missing")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise StateCaptureError(f"{label} must be an HTTP(S) URL without credentials, query, or fragment")
    return value.rstrip("/")


def _validated_relative_path(path: str, *, label: str) -> SplitResult:
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or not path.startswith("/") or parsed.fragment:
        raise StateCaptureError(f"{label} path must be provider-relative")
    if any(segment in {".", ".."} for segment in parsed.path.split("/")):
        raise StateCaptureError(f"{label} path must not contain traversal segments")
    return parsed


def _joined_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _proxy_token(control_payload: Mapping[str, Any]) -> str | None:
    raw_twin_run = control_payload.get("twin_run")
    if not isinstance(raw_twin_run, dict):
        return None
    twin_run = cast(dict[str, Any], raw_twin_run)
    value = twin_run.get("proxy_token")
    return value if isinstance(value, str) and value else None


def _verifier_headers(target: TrustedProviderTarget) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "arga-twins-benchmark-verifier/0.1"}
    headers.update(provider_request_headers(target.provider_name, target.env))
    return headers


def _json_value(value: object, *, label: str) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [_json_value(item, label=label) for item in cast(list[object], value)]
    if isinstance(value, dict):
        raw = cast(dict[object, object], value)
        if not all(isinstance(key, str) for key in raw):
            raise StateCaptureError(f"{label} contains a JSON object with a non-string key")
        return {cast(str, key): _json_value(item, label=label) for key, item in raw.items()}
    raise StateCaptureError(f"{label} contains non-JSON value {type(value).__name__}")
