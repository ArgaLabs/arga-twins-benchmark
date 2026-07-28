from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from arga_twins_benchmark.catalog import validate_catalog
from arga_twins_benchmark.evaluation.deterministic import CanonicalResource
from arga_twins_benchmark.evaluation.protocol import JsonValue
from arga_twins_benchmark.evaluation.state_capture import (
    CapturedProviderState,
    CapturedQueryState,
    StateCaptureError,
    TrustedStateCapturer,
    TrustedStateSnapshot,
    canonicalize_query_results,
    diff_canonical_resources,
    diff_trusted_states,
    normalize_json,
    snapshot_query_request_body,
    snapshot_query_uses_control_plane,
    targets_from_control,
)
from arga_twins_benchmark.specs.models import SnapshotQuerySpec, VerificationSpec


def control_payload() -> dict[str, Any]:
    return {
        "protocol": "arga-bench-control/1",
        "instance_id": "review-1",
        "run_id": "run-1",
        "twin_run": {
            "run_id": "run-1",
            "proxy_token": "verifier-proxy-secret",
            "twins": {
                "github": {
                    "base_url": "https://pub-github.example",
                    "admin_url": "https://admin-github.example",
                    "env_vars": {"GITHUB_TOKEN": "provider-secret"},
                }
            },
        },
    }


def snapshot_query(
    *,
    query_id: str = "reviews",
    path: str = "/repos/acme/app/pulls/7/reviews",
    canonicalizer: str = "github.reviews",
) -> SnapshotQuerySpec:
    return SnapshotQuerySpec.model_validate(
        {
            "id": query_id,
            "provider_role": "code_host",
            "method": "GET",
            "path": path,
            "canonicalizer": canonicalizer,
        }
    )


def test_targets_are_resolved_only_from_trusted_control_payload() -> None:
    targets = targets_from_control(control_payload(), roles={"code_host": "github"})

    assert set(targets) == {"github"}
    assert targets["github"].provider_role == "code_host"
    assert targets["github"].base_url == "https://pub-github.example"
    assert targets["github"].admin_url == "https://admin-github.example"


def test_capture_uses_admin_origin_for_whole_state_and_data_origin_for_queries() -> None:
    seen: list[tuple[str, str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                request.url.host or "",
                request.url.raw_path.decode(),
                request.headers.get("cookie", ""),
            )
        )
        assert request.headers["authorization"] == "Bearer provider-secret"
        if request.url.host == "admin-github.example":
            return httpx.Response(200, json={"counts": {"reviews": 0}})
        return httpx.Response(200, json=[{"id": 41, "state": "REQUEST_CHANGES"}])

    async def capture() -> TrustedStateSnapshot:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await TrustedStateCapturer(client=client).capture(
                control_payload(),
                roles={"code_host": "github"},
                snapshot_queries=[snapshot_query()],
            )

    snapshot = asyncio.run(capture())

    assert seen == [
        ("GET", "admin-github.example", "/admin/state", "arga_env_proxy_token=verifier-proxy-secret"),
        (
            "GET",
            "pub-github.example",
            "/repos/acme/app/pulls/7/reviews",
            "arga_env_proxy_token=verifier-proxy-secret",
        ),
    ]
    assert snapshot.providers["github"].state == {"counts": {"reviews": 0}}
    assert snapshot.queries["reviews"].body == [{"id": 41, "state": "REQUEST_CHANGES"}]
    rendered_artifact = json.dumps(snapshot.artifact_payload())
    assert "admin-github.example" not in rendered_artifact
    assert "provider-secret" not in rendered_artifact
    assert "verifier-proxy-secret" not in rendered_artifact


@pytest.mark.parametrize("transient_status", [429, 502, 503, 504])
def test_capture_retries_only_retryable_verifier_statuses(transient_status: int) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(transient_status, json={"error": "transient"})
        return httpx.Response(200, json={"ok": True})

    async def capture() -> TrustedStateSnapshot:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await TrustedStateCapturer(
                client=client,
                max_attempts=3,
                retry_base_delay_seconds=0,
            ).capture(control_payload(), roles={"code_host": "github"})

    snapshot = asyncio.run(capture())

    assert attempts == 3
    assert snapshot.providers["github"].state == {"ok": True}


def test_capture_retries_transport_errors_but_not_permanent_http_errors() -> None:
    transport_attempts = 0

    def transient_handler(request: httpx.Request) -> httpx.Response:
        nonlocal transport_attempts
        transport_attempts += 1
        if transport_attempts == 1:
            raise httpx.ConnectError("temporary verifier connection failure", request=request)
        return httpx.Response(200, json={"ok": True})

    async def capture_transient() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(transient_handler)) as client:
            await TrustedStateCapturer(
                client=client,
                max_attempts=2,
                retry_base_delay_seconds=0,
            ).capture(control_payload(), roles={"code_host": "github"})

    asyncio.run(capture_transient())
    assert transport_attempts == 2

    permanent_attempts = 0

    def permanent_handler(_: httpx.Request) -> httpx.Response:
        nonlocal permanent_attempts
        permanent_attempts += 1
        return httpx.Response(400, json={"error": "invalid verifier request"})

    async def capture_permanent() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(permanent_handler)) as client:
            await TrustedStateCapturer(
                client=client,
                max_attempts=3,
                retry_base_delay_seconds=0,
            ).capture(control_payload(), roles={"code_host": "github"})

    with pytest.raises(StateCaptureError, match="HTTP 400"):
        asyncio.run(capture_permanent())
    assert permanent_attempts == 1


def test_capture_reports_exhausted_transient_attempts() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(504, json={"error": "still unavailable"})

    async def capture() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await TrustedStateCapturer(
                client=client,
                max_attempts=3,
                retry_base_delay_seconds=0,
            ).capture(control_payload(), roles={"code_host": "github"})

    with pytest.raises(StateCaptureError, match="HTTP 504"):
        asyncio.run(capture())
    assert attempts == 3


@pytest.mark.parametrize(
    ("provider", "header", "expected"),
    [
        ("github", "authorization", "Bearer ghp_scenario_seed"),
        ("gitlab", "private-token", "glpat-gitlab-twin-token"),
        ("gmail", "authorization", "Bearer ya29.gmail-twin-owner"),
        ("google_calendar", "authorization", "Bearer test-token"),
        ("google_drive", "authorization", "Bearer ya29.drive-twin-owner"),
        ("jira", "authorization", "Bearer jira_default_seed_token"),
        ("linear", "authorization", "lin_api_twin_owner_personal_key_0001"),
        ("notion", "authorization", "Bearer secret_notion-twin_seed"),
        ("slack", "authorization", "Bearer xoxb-F9SXMECOSFOGYR3XKXWN"),
        ("stripe", "authorization", "Bearer sk_test_twin_scenario"),
        ("discord", "authorization", "Bot fake-bot-token"),
    ],
)
def test_capture_uses_provider_native_default_authentication(
    provider: str,
    header: str,
    expected: str,
) -> None:
    payload = control_payload()
    twins = cast(dict[str, Any], cast(dict[str, Any], payload["twin_run"])["twins"])
    twins.clear()
    twins[provider] = {
        "base_url": f"https://pub-{provider}.example",
        "admin_url": f"https://admin-{provider}.example",
        "env_vars": {},
    }
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["header"] = request.headers[header]
        return httpx.Response(200, json={"ok": True})

    async def capture() -> TrustedStateSnapshot:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await TrustedStateCapturer(client=client).capture(
                payload,
                roles={"provider": provider},
            )

    asyncio.run(capture())

    assert observed == {"header": expected}


def test_snapshot_query_rejects_external_and_mutating_control_plane_paths() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async def capture(query: SnapshotQuerySpec) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await TrustedStateCapturer(client=client).capture(
                control_payload(),
                roles={"code_host": "github"},
                snapshot_queries=[query],
            )

    with pytest.raises(StateCaptureError, match="provider-relative"):
        asyncio.run(capture(snapshot_query(path="https://api.github.com/repos/acme/app")))
    with pytest.raises(StateCaptureError, match="control-plane"):
        asyncio.run(capture(snapshot_query(path="/_twin/seed")))


def test_manifest_declared_state_route_is_sent_only_to_admin_origin() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host or "")
        return httpx.Response(200, json={"reviews": 0})

    async def capture() -> TrustedStateSnapshot:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await TrustedStateCapturer(client=client).capture(
                control_payload(),
                roles={"code_host": "github"},
                snapshot_queries=[snapshot_query(path="/admin/state")],
            )

    snapshot = asyncio.run(capture())

    assert hosts == ["admin-github.example", "admin-github.example"]
    assert snapshot.queries["reviews"].body == {"reviews": 0}


@pytest.mark.parametrize("legacy_status", [404, 410])
def test_provider_specific_admin_state_path_and_fallback(legacy_status: int) -> None:
    payload = control_payload()
    twins = cast(dict[str, Any], cast(dict[str, Any], payload["twin_run"])["twins"])
    twins.clear()
    twins.update(
        {
            "google_calendar": {
                "base_url": "https://pub-calendar.example",
                "admin_url": "https://admin-calendar.example",
            },
            "gmail": {
                "base_url": "https://pub-gmail.example",
                "admin_url": "https://admin-gmail.example",
            },
        }
    )
    paths: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host or ""
        path = request.url.path
        paths.append((host, path))
        if host == "admin-gmail.example" and path == "/admin/state":
            return httpx.Response(legacy_status, json={"error": "legacy alias unavailable"})
        return httpx.Response(200, json={"ok": True})

    async def capture() -> TrustedStateSnapshot:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await TrustedStateCapturer(client=client).capture(
                payload,
                roles={"calendar": "google_calendar", "email": "gmail"},
            )

    snapshot = asyncio.run(capture())

    assert paths == [
        ("admin-gmail.example", "/admin/state"),
        ("admin-gmail.example", "/inspect"),
        ("admin-calendar.example", "/_admin/state"),
    ]
    assert set(snapshot.providers) == {"gmail", "google_calendar"}


def test_all_catalog_snapshot_paths_and_post_bodies_are_supported() -> None:
    catalog_root = Path(__file__).resolve().parents[3] / "benchmark"
    verifications = [
        document.model for document in validate_catalog(catalog_root) if isinstance(document.model, VerificationSpec)
    ]
    assert len(verifications) == 48

    control_query_count = 0
    linear_post_count = 0
    for verification in verifications:
        for query in verification.deterministic.snapshot_queries:
            control_query_count += int(snapshot_query_uses_control_plane(query))
            body = snapshot_query_request_body(query)
            if query.method == "GET":
                assert body is None
            elif query.path == "/graphql":
                linear_post_count += 1
                assert body is not None
                assert body["operationName"] == "ArgaBenchmarkSnapshot"
                assert "issues(first: 250)" in str(body["query"])
            else:
                assert body == {}

    assert control_query_count == 16
    assert linear_post_count == 9


def test_normalization_ignores_reordering_of_identity_keyed_lists() -> None:
    left: JsonValue = {"issues": [{"id": "B", "state": "open"}, {"id": "A", "state": "closed"}]}
    right: JsonValue = {"issues": [{"state": "closed", "id": "A"}, {"state": "open", "id": "B"}]}

    assert normalize_json(left) == normalize_json(right)


def test_raw_diff_is_stable_and_reports_create_update_delete() -> None:
    before = TrustedStateSnapshot(
        providers={
            "linear": CapturedProviderState(
                "linear",
                "issue_tracker",
                {
                    "issues": [
                        {"id": "keep", "state": "open"},
                        {"id": "delete", "state": "open"},
                    ]
                },
            )
        }
    )
    after = TrustedStateSnapshot(
        providers={
            "linear": CapturedProviderState(
                "linear",
                "issue_tracker",
                {
                    "issues": [
                        {"id": "create", "state": "open"},
                        {"id": "keep", "state": "closed"},
                    ]
                },
            )
        }
    )

    deltas = diff_trusted_states(before, after)

    assert [(delta.operation, delta.json_pointer) for delta in deltas] == [
        ("create", "/issues/id=create"),
        ("delete", "/issues/id=delete"),
        ("update", "/issues/id=keep/state"),
    ]


def test_query_canonicalizers_merge_projections_and_fail_closed_when_missing() -> None:
    snapshot = TrustedStateSnapshot(
        providers={},
        queries={
            "review": CapturedQueryState(
                "review",
                "github",
                "code_host",
                "GET",
                "/reviews/7",
                "github.review",
                200,
                {"id": "r-1", "state": "REQUEST_CHANGES"},
            ),
            "body": CapturedQueryState(
                "body",
                "github",
                "code_host",
                "GET",
                "/reviews/7/body",
                "github.review_body",
                200,
                {"id": "r-1", "body": "Blocking"},
            ),
        },
    )

    def fields_from_body(capture: CapturedQueryState) -> list[CanonicalResource]:
        body = cast(Mapping[str, object], capture.body)
        resource_id = str(body["id"])
        fields = {key: value for key, value in body.items() if key != "id"}
        return [CanonicalResource(capture.provider_role, "pull_request_review", resource_id, fields)]

    resources = canonicalize_query_results(
        snapshot,
        canonicalizers={
            "github.review": fields_from_body,
            "github.review_body": fields_from_body,
        },
    )

    assert resources == [
        CanonicalResource(
            "code_host",
            "pull_request_review",
            "r-1",
            {"state": "REQUEST_CHANGES", "body": "Blocking"},
        )
    ]
    with pytest.raises(StateCaptureError, match="unregistered canonicalizer"):
        canonicalize_query_results(snapshot, canonicalizers={"github.review": fields_from_body})


def test_canonical_resource_diff_produces_semantic_mutations() -> None:
    before = [
        CanonicalResource("payments", "price", "price-old", {"nickname": None, "lookup_key": None}),
        CanonicalResource("payments", "price", "price-deleted", {"nickname": "legacy"}),
    ]
    after = [
        CanonicalResource(
            "payments",
            "price",
            "price-old",
            {"nickname": "pro-monthly", "lookup_key": "pro_monthly"},
        ),
        CanonicalResource("payments", "price", "price-created", {"nickname": "new"}),
    ]

    mutations = diff_canonical_resources(before, after)

    assert [(mutation.resource_id, mutation.operation) for mutation in mutations] == [
        ("price-created", "create"),
        ("price-deleted", "delete"),
        ("price-old", "update"),
    ]
    assert mutations[-1].before == {"nickname": None, "lookup_key": None}
    assert mutations[-1].after == {"nickname": "pro-monthly", "lookup_key": "pro_monthly"}


def test_duplicate_provider_roles_and_mismatched_capture_contracts_fail_closed() -> None:
    with pytest.raises(StateCaptureError, match="more than one role"):
        targets_from_control(
            control_payload(),
            roles={"code_host": "github", "repository": "github"},
        )

    before = TrustedStateSnapshot(
        providers={"github": CapturedProviderState("github", "code_host", {"reviews": 0})}
    )
    after = TrustedStateSnapshot(
        providers={"github": CapturedProviderState("github", "repository", {"reviews": 0})}
    )
    with pytest.raises(StateCaptureError, match="provider role changed"):
        diff_trusted_states(before, after)


def test_trusted_snapshot_artifact_round_trip_is_exact() -> None:
    original = TrustedStateSnapshot(
        providers={
            "github": CapturedProviderState(
                "github",
                "code_host",
                {"pulls": [{"number": 7, "state": "open"}]},
            )
        },
        queries={
            "pulls": CapturedQueryState(
                "pulls",
                "github",
                "code_host",
                "GET",
                "/repos/acme/demo/pulls",
                "github_pulls_stable",
                200,
                [{"number": 7, "state": "open"}],
            )
        },
    )

    restored = TrustedStateSnapshot.from_artifact_payload(original.artifact_payload())

    assert restored == original


@pytest.mark.parametrize(
    ("case", "match"),
    [
        ("extra_provider_field", "exactly provider_role and state"),
        ("boolean_status", "invalid status_code"),
        ("mismatched_role", "mismatched provider role"),
        ("unknown_provider", "unknown provider"),
    ],
)
def test_trusted_snapshot_artifact_loader_rejects_ambiguous_evidence(
    case: str,
    match: str,
) -> None:
    payload = cast(
        dict[str, Any],
        TrustedStateSnapshot(
            providers={"github": CapturedProviderState("github", "code_host", {"pulls": []})},
            queries={
                "pulls": CapturedQueryState(
                    "pulls",
                    "github",
                    "code_host",
                    "GET",
                    "/repos/acme/demo/pulls",
                    "github_pulls_stable",
                    200,
                    [],
                )
            },
        ).artifact_payload(),
    )
    providers = cast(dict[str, dict[str, Any]], payload["providers"])
    queries = cast(dict[str, dict[str, Any]], payload["queries"])
    if case == "extra_provider_field":
        providers["github"]["unexpected"] = True
    elif case == "boolean_status":
        queries["pulls"]["status_code"] = True
    elif case == "mismatched_role":
        queries["pulls"]["provider_role"] = "other"
    else:
        queries["pulls"]["provider_name"] = "gitlab"

    with pytest.raises(StateCaptureError, match=match):
        TrustedStateSnapshot.from_artifact_payload(payload)
