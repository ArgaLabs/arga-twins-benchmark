from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast

import httpx
import pytest

from arga_twins_benchmark.providers import (
    ProviderGateway,
    ProviderGatewayConfigurationError,
    ProviderInfrastructureError,
    ProviderTraceRecord,
)
from arga_twins_benchmark.providers.gateway import provider_tool_input_fingerprints


def _access(*providers: str) -> dict[str, dict[str, object]]:
    return {
        provider: {
            "base_url": f"https://pub-run--{provider}.sandbox.argalabs.com",
            "env": {},
        }
        for provider in providers
    }


def _run(gateway: ProviderGateway, tool_input: dict[str, object]) -> dict[str, object]:
    return asyncio.run(gateway.execute(tool_input))


def _preview_proxy_failure_body(code: str) -> dict[str, object]:
    signatures = {
        "authentication_required": ("runtime", "auth_failure", False),
        "environment_deploy_failed": ("deploy", "deploy_failure", False),
        "environment_destroyed": ("cleanup", "proxy_failure", False),
        "environment_not_ready": ("deploy", "deploy_failure", True),
        "invalid_token": ("runtime", "auth_failure", False),
        "pr_preview_not_found": ("runtime", "proxy_failure", False),
        "preview_host_not_found": ("runtime", "proxy_failure", False),
        "proxy_runtime_error": ("runtime", "proxy_failure", False),
        "run_not_found": ("runtime", "proxy_failure", False),
        "surface_not_ready": ("deploy", "deploy_failure", True),
        "upstream_connect_error": ("runtime", "connect_error", True),
        "upstream_connect_timeout": ("runtime", "connect_error", True),
        "upstream_http_error": ("runtime", "proxy_failure", True),
        "upstream_timeout": ("runtime", "timeout", True),
        "user_not_found": ("runtime", "auth_failure", False),
    }
    phase, kind, retryable = signatures[code]
    return {
        "code": code,
        "detail": f"preview proxy failure: {code}",
        "failure": {
            "code": code,
            "phase": phase,
            "kind": kind,
            "retryable": retryable,
        },
        "request_id": "request-1",
    }


def _preview_proxy_failure_response(status_code: int, code: str) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers={
            "x-arga-preview-proxy-failure": "1",
            "x-request-id": "request-1",
        },
        json=_preview_proxy_failure_body(code),
    )


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
def test_applies_provider_default_authentication(provider: str, header: str, expected: str) -> None:
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["header"] = request.headers[header]
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access(provider), client=client)

    result = _run(gateway, {"provider": provider, "method": "GET", "path": "/v1/resource"})

    assert result["ok"] is True
    assert observed == {"header": expected}
    asyncio.run(client.aclose())


def test_exposes_one_dynamic_tool_schema() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(200)))
    gateway = ProviderGateway(
        _access("linear", "slack"),
        {"issue_tracker": "linear", "team_chat": "slack"},
        client=client,
    )

    tool = gateway.tool_definition

    assert tool["name"] == "provider_api"
    schema = cast(dict[str, object], tool["input_schema"])
    properties = cast(dict[str, object], schema["properties"])
    provider_property = cast(dict[str, object], properties["provider"])
    assert provider_property["enum"] == ["issue_tracker", "linear", "slack", "team_chat"]
    assert schema["required"] == ["provider", "method", "path"]
    asyncio.run(client.aclose())


def test_resolves_role_records_graphql_operation_and_stable_effective_query() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["body"] = request.content
        observed["authorization"] = request.headers["authorization"]
        return httpx.Response(
            201,
            headers={"Content-Type": "application/json", "ETag": '"issue-1"', "Set-Cookie": "secret=1"},
            json={"data": {"issueCreate": {"success": True}}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(
        _access("linear"),
        {"issue_tracker": "linear"},
        client=client,
    )
    body = {
        "operationName": "CreateIssue",
        "query": "mutation CreateIssue($input: IssueCreateInput!) { issueCreate(input: $input) { success } }",
        "variables": {"input": {"title": "One"}},
    }

    tool_input: dict[str, object] = {
        "provider": "issue_tracker",
        "method": "POST",
        "path": "/graphql?z=9",
        "query": {"z": ["2", "1"], "a": "hello world"},
        "body": body,
        "headers": {"X-Private-Marker": "private-fingerprint-value"},
    }
    result = _run(gateway, tool_input)

    assert observed["url"] == "https://pub-run--linear.sandbox.argalabs.com/graphql?a=hello+world&z=1&z=2&z=9"
    assert observed["authorization"] == "lin_api_twin_owner_personal_key_0001"
    assert result["requested_provider"] == "issue_tracker"
    assert result["provider"] == "linear"
    assert result["status_code"] == 201
    assert result["path"] == "/graphql?a=hello+world&z=1&z=2&z=9"
    assert result["body"] == {"data": {"issueCreate": {"success": True}}}
    assert result["headers"] == {"content-length": "41", "content-type": "application/json", "etag": '"issue-1"'}

    trace = gateway.trace_records[0]
    assert trace.requested_provider == "issue_tracker"
    assert trace.provider == "linear"
    assert trace.path == "/graphql?a=hello+world&z=1&z=2&z=9"
    assert trace.operation == "issueCreate"
    assert trace.operation_type == "mutation"
    assert trace.status_code == 201
    expected_fingerprints = provider_tool_input_fingerprints(
        tool_input,
        resolved_provider="linear",
        effective_path="/graphql?a=hello+world&z=1&z=2&z=9",
    )
    assert (trace.request_fingerprint, trace.action_fingerprint) == expected_fingerprints
    assert trace.request_fingerprint is not None and len(trace.request_fingerprint) == 64
    assert trace.action_fingerprint is not None and len(trace.action_fingerprint) == 64
    assert trace.attempt_fingerprint is not None and len(trace.attempt_fingerprint) == 64
    assert "private-fingerprint-value" not in repr(trace)
    asyncio.run(client.aclose())


def test_fingerprints_are_stable_across_mapping_order_and_distinguish_arguments() -> None:
    first: dict[str, object] = {
        "provider": "issue_tracker",
        "method": "POST",
        "path": "/graphql?z=9",
        "query": {"z": ["2", "1"], "a": "hello world"},
        "body": {
            "operationName": "CreateIssue",
            "query": "mutation CreateIssue($input: IssueCreateInput!) { issueCreate(input: $input) { success } }",
            "variables": {"input": {"title": "One", "priority": 1}},
        },
    }
    reordered: dict[str, object] = {
        "body": {
            "variables": {"input": {"priority": 1, "title": "One"}},
            "query": "mutation CreateIssue($input: IssueCreateInput!) { issueCreate(input: $input) { success } }",
            "operationName": "CreateIssue",
        },
        "query": {"a": "hello world", "z": ["1", "2"]},
        "path": "/graphql?z=9",
        "method": "POST",
        "provider": "issue_tracker",
    }
    changed_variables = {
        **first,
        "body": {
            **cast(dict[str, object], first["body"]),
            "variables": {"input": {"title": "Two", "priority": 1}},
        },
    }
    changed_query = {**first, "query": {"z": ["2", "1"], "a": "different"}}

    first_fingerprints = provider_tool_input_fingerprints(first, resolved_provider="linear")

    assert provider_tool_input_fingerprints(reordered, resolved_provider="linear") == first_fingerprints
    assert provider_tool_input_fingerprints(changed_variables, resolved_provider="linear") != first_fingerprints
    assert provider_tool_input_fingerprints(changed_query, resolved_provider="linear") != first_fingerprints


def test_action_fingerprint_ignores_retry_and_idempotency_headers_only() -> None:
    base: dict[str, object] = {
        "provider": "stripe",
        "method": "POST",
        "path": "/v1/prices/price_1",
        "body": {"nickname": "Pro Monthly", "lookup_key": "pro_monthly_usd"},
    }
    first = {
        **base,
        "headers": {
            "Idempotency-Key": "attempt-one",
            "If-Match": '"revision-one"',
            "X-Retry-Count": "1",
            "X-Workflow": "catalog-normalization",
        },
    }
    retried = {
        **base,
        "headers": {
            "Idempotency-Key": "attempt-two",
            "If-Match": '"revision-two"',
            "X-Retry-Count": "2",
            "X-Workflow": "catalog-normalization",
        },
    }
    different_action_header = {
        **retried,
        "headers": {
            **cast(dict[str, str], retried["headers"]),
            "X-Workflow": "different-workflow",
        },
    }

    first_request, first_action = provider_tool_input_fingerprints(first, resolved_provider="stripe")
    retry_request, retry_action = provider_tool_input_fingerprints(retried, resolved_provider="stripe")
    _, different_action = provider_tool_input_fingerprints(different_action_header, resolved_provider="stripe")

    assert first_request != retry_request
    assert first_action == retry_action
    assert different_action != first_action


def test_provider_trace_fingerprint_fields_are_backward_compatible() -> None:
    trace = ProviderTraceRecord(
        sequence=1,
        started_at="2030-01-01T00:00:00+00:00",
        requested_provider="code_host",
        provider="github",
        method="GET",
        path="/repos/acme/app",
        operation=None,
        operation_type=None,
        status_code=200,
        latency_ms=1,
        response_bytes=2,
        truncated=False,
        error=None,
    )

    assert trace.request_fingerprint is None
    assert trace.action_fingerprint is None
    assert trace.attempt_fingerprint is None


@pytest.mark.parametrize(
    "path",
    [
        "https://api.github.com/repos/acme/app",
        "//api.github.com/repos/acme/app",
        "/%2F%2Fevil.example/path",
        "/safe/../admin/state",
        "/safe/%2e%2e/admin/state",
        "/safe/%252e%252e/admin/state",
        "/admin/state",
        "/%61dmin/state",
        "/_admin/state",
        "/_twin/inspect",
        "/_ui/repos/acme/app",
        "/inspect",
        "/reset",
        "/control-plane/status",
        "/control/status",
    ],
)
def test_rejects_external_traversal_and_control_plane_paths_without_network(path: str) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access("github"), {"code_host": "github"}, client=client)

    result = _run(gateway, {"provider": "code_host", "method": "GET", "path": path})

    assert result["ok"] is False
    assert result["status_code"] is None
    assert calls == 0
    trace = gateway.trace_records[0]
    assert trace.requested_provider == "code_host"
    assert trace.provider == "github"
    assert trace.error
    assert trace.attempt_fingerprint is not None
    assert len(trace.attempt_fingerprint) == 64
    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/api",
        "/docs",
        "/openapi.json",
        "/swagger",
        "/api/docs",
        "/api/openapi.yaml",
        "/health",
        "/api/healthz",
        "/metrics",
        "/api/readiness",
        "/.well-known/openapi",
        "/.%77ell-known/schema",
        "/api/.well-known/openapi",
        "/api/_admin/state",
        "/api/grader/result",
    ],
)
def test_candidate_safe_surface_rejects_twin_ui_schema_and_control_roots(path: str) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access("github"), client=client)

    result = _run(gateway, {"provider": "github", "method": "GET", "path": path})

    assert result["ok"] is False
    assert result["error"]
    assert calls == 0
    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    "path",
    [
        "/repos/acme/app/contents/openapi.json",
        "/repos/acme/app/contents/docs",
        "/repos/acme/admin/issues/schema",
        "/repos/acme/app/contents/health",
        "/api/v4/projects/acme/repository/files/openapi.json",
    ],
)
def test_candidate_safe_surface_allows_schema_like_names_in_provider_data_paths(path: str) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"path": path})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access("github"), client=client)

    result = _run(gateway, {"provider": "github", "method": "GET", "path": path})

    assert result["ok"] is True
    assert calls == 1
    asyncio.run(client.aclose())


def test_candidate_safe_surface_rejects_graphql_introspection_without_network() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access("linear"), client=client)

    result = _run(
        gateway,
        {
            "provider": "linear",
            "method": "POST",
            "path": "/graphql",
            "body": {"query": "query Introspection { __schema { types { name } } }"},
        },
    )

    assert result["ok"] is False
    assert "introspection" in str(result["error"])
    assert calls == 0
    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    "path",
    [
        "/graphql?query=query%20Introspection%20%7B%20__schema%20%7B%20types%20%7B%20name%20%7D%20%7D%20%7D",
        "/graphql?query=query%20Introspection%20%7B%20%255F%255Ftype(name%3A%22Issue%22)%20%7Bname%7D%20%7D",
    ],
)
def test_candidate_safe_surface_rejects_encoded_graphql_query_introspection(path: str) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access("linear"), client=client)

    result = _run(gateway, {"provider": "linear", "method": "GET", "path": path})

    assert result["ok"] is False
    assert "introspection" in str(result["error"])
    assert calls == 0
    asyncio.run(client.aclose())


def test_candidate_safe_surface_allows_graphql_typename() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"data": {}}))
    )
    gateway = ProviderGateway(_access("linear"), client=client)

    result = _run(
        gateway,
        {
            "provider": "linear",
            "method": "POST",
            "path": "/graphql",
            "body": {"query": "query { issues { nodes { __typename id } } }"},
        },
    )

    assert result["ok"] is True
    asyncio.run(client.aclose())


def test_legacy_surface_allows_root_schema_but_still_blocks_control_plane() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(
        _access("github"),
        candidate_safe_surface=False,
        client=client,
    )

    assert _run(gateway, {"provider": "github", "method": "GET", "path": "/"})["ok"] is True
    assert _run(gateway, {"provider": "github", "method": "GET", "path": "/openapi.json"})["ok"] is True
    blocked = _run(gateway, {"provider": "github", "method": "GET", "path": "/_admin/state"})

    assert blocked["ok"] is False
    assert calls == 2
    asyncio.run(client.aclose())


def test_provider_call_limit_is_separate_and_rejects_excess_network_calls() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access("github"), max_calls=1, client=client)

    first = _run(gateway, {"provider": "github", "method": "GET", "path": "/repos/acme/app"})
    second = _run(gateway, {"provider": "github", "method": "GET", "path": "/repos/acme/app"})

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["error"] == "provider_api call limit of 1 has been reached"
    assert calls == 1
    assert len(gateway.trace_records) == 2
    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    "tool_input",
    [
        {"provider": "github", "method": "HEAD", "path": "/repos/acme/app"},
        {"provider": "other", "method": "GET", "path": "/repos/acme/app"},
        {"provider": "github", "method": "GET", "path": "/repos/acme/app", "query": {"x": {"nested": 1}}},
        {
            "provider": "github",
            "method": "GET",
            "path": "/repos/acme/app",
            "headers": {"Authorization": "Bearer external"},
        },
        {
            "provider": "github",
            "method": "GET",
            "path": "/repos/acme/app",
            "headers": {"X-Original-URL": "/_admin/state"},
        },
        {
            "provider": "github",
            "method": "GET",
            "path": "/repos/acme/app",
            "headers": {"X-Forwarded-Host": "api.github.com"},
        },
        {
            "provider": "github",
            "method": "GET",
            "path": "/repos/acme/app",
            "headers": {"X-Arga-Proxy-Token": "control-secret"},
        },
    ],
)
def test_rejects_invalid_candidate_inputs(tool_input: dict[str, object]) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access("github"), client=client)

    result = _run(gateway, tool_input)

    assert result["ok"] is False
    assert result["error"]
    assert calls == 0
    assert len(gateway.trace_records) == 1
    asyncio.run(client.aclose())


def test_candidate_cannot_forge_preview_proxy_request_id() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _preview_proxy_failure_response(410, "environment_destroyed")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access("github"), client=client)

    result = _run(
        gateway,
        {
            "provider": "github",
            "method": "GET",
            "path": "/repos/acme/app",
            "headers": {"X-Request-ID": "request-1"},
        },
    )

    assert result["error"] == "request header 'X-Request-ID' cannot be supplied by the candidate"
    assert calls == 0
    asyncio.run(client.aclose())


def test_bounds_response_bytes_and_marks_truncation() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                content=b'{"large":"abcdefghijklmnopqrstuvwxyz"}',
            )
        )
    )
    gateway = ProviderGateway(_access("github"), max_response_bytes=12, client=client)

    result = _run(gateway, {"provider": "github", "method": "GET", "path": "/repos/acme/app"})

    assert result["ok"] is True
    assert result["truncated"] is True
    assert result["body"] == {"encoding": "base64", "data": "eyJsYXJnZSI6ImFi"}
    trace = gateway.trace_records[0]
    assert trace.response_bytes == 12
    assert trace.truncated is True
    asyncio.run(client.aclose())


def test_stripe_defaults_to_flattened_form_body_and_allows_idempotency_header() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["content_type"] = request.headers["content-type"]
        observed["idempotency_key"] = request.headers["idempotency-key"]
        observed["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "price_1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access("stripe"), client=client)

    result = _run(
        gateway,
        {
            "provider": "stripe",
            "method": "POST",
            "path": "/v1/prices/price_1",
            "headers": {"Idempotency-Key": "normalize-CAT-7900"},
            "body": {
                "lookup_key": "pro_monthly_usd",
                "metadata": {"request": "CAT-7900"},
                "nickname": "Pro Monthly",
            },
        },
    )

    assert result["ok"] is True
    assert observed == {
        "content_type": "application/x-www-form-urlencoded",
        "idempotency_key": "normalize-CAT-7900",
        "body": "lookup_key=pro_monthly_usd&metadata%5Brequest%5D=CAT-7900&nickname=Pro+Monthly",
    }
    asyncio.run(client.aclose())


def test_candidate_env_overrides_twin_default_without_leaking_into_trace() -> None:
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["authorization"] = request.headers["authorization"]
        return httpx.Response(200, text="ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(
        {
            "github": {
                "base_url": "https://pub-run--github.sandbox.argalabs.com",
                "env": {"GITHUB_TOKEN": "candidate-provider-token"},
            }
        },
        client=client,
    )

    result = _run(gateway, {"provider": "github", "method": "GET", "path": "/user"})

    assert observed["authorization"] == "Bearer candidate-provider-token"
    assert "candidate-provider-token" not in repr(result)
    assert "candidate-provider-token" not in repr(gateway.trace_records)
    asyncio.run(client.aclose())


def test_does_not_follow_provider_redirects() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"Location": "https://api.github.com/repos/acme/app"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    gateway = ProviderGateway(_access("github"), client=client)

    result = _run(gateway, {"provider": "github", "method": "GET", "path": "/repos/acme/app"})

    assert result["status_code"] == 302
    assert result["ok"] is False
    assert calls == 1
    asyncio.run(client.aclose())


def test_network_error_is_sanitized_and_traced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("could not reach secret endpoint", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access("github"), client=client)

    result = _run(gateway, {"provider": "github", "method": "GET", "path": "/repos/acme/app"})

    assert result["error"] == "provider request failed: ConnectError"
    assert gateway.trace_records[0].error == "provider request failed: ConnectError"
    asyncio.run(client.aclose())


def test_repeated_connect_errors_trip_at_conservative_threshold() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("could not reach secret endpoint", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access("github"), client=client)

    async def exercise() -> None:
        for _ in range(2):
            result = await gateway.execute({"provider": "github", "method": "GET", "path": "/repos/acme/app"})
            assert result["error"] == "provider request failed: ConnectError"
        with pytest.raises(ProviderInfrastructureError) as exc_info:
            await gateway.execute({"provider": "github", "method": "GET", "path": "/repos/acme/app"})
        assert exc_info.value.code == "transport_connect_error"
        assert exc_info.value.status_code is None
        assert exc_info.value.consecutive_failures == 3

    asyncio.run(exercise())

    assert len(gateway.trace_records) == 3
    assert [record.error for record in gateway.trace_records] == [
        "provider request failed: ConnectError",
        "provider request failed: ConnectError",
        "provider request failed: ConnectError",
    ]
    asyncio.run(client.aclose())


def test_repeated_timeouts_trip_at_conservative_threshold() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret timeout detail", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access("github"), client=client)

    async def exercise() -> None:
        for _ in range(2):
            result = await gateway.execute({"provider": "github", "method": "GET", "path": "/repos/acme/app"})
            assert result["error"] == "provider request timed out"
        with pytest.raises(ProviderInfrastructureError) as exc_info:
            await gateway.execute({"provider": "github", "method": "GET", "path": "/repos/acme/app"})
        assert exc_info.value.code == "transport_timeout"
        assert exc_info.value.status_code is None
        assert exc_info.value.consecutive_failures == 3

    asyncio.run(exercise())

    assert len(gateway.trace_records) == 3
    assert [record.error for record in gateway.trace_records] == [
        "provider request timed out",
        "provider request timed out",
        "provider request timed out",
    ]
    asyncio.run(client.aclose())


def test_repeated_local_protocol_errors_remain_tool_visible_and_do_not_trip_circuit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.LocalProtocolError("invalid local request framing", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access("github"), client=client)

    async def exercise() -> list[dict[str, object]]:
        return [
            await gateway.execute({"provider": "github", "method": "GET", "path": "/repos/acme/app"}) for _ in range(4)
        ]

    results = asyncio.run(exercise())

    assert [result["error"] for result in results] == [
        "provider request failed: LocalProtocolError",
    ] * 4
    assert len(gateway.trace_records) == 4
    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    ("status_code", "code"),
    [
        (401, "authentication_required"),
        (409, "environment_deploy_failed"),
        (410, "environment_destroyed"),
        (409, "environment_not_ready"),
        (401, "invalid_token"),
        (404, "pr_preview_not_found"),
        (404, "preview_host_not_found"),
        (500, "proxy_runtime_error"),
        (404, "run_not_found"),
        (409, "surface_not_ready"),
        (502, "upstream_connect_error"),
        (502, "upstream_connect_timeout"),
        (502, "upstream_http_error"),
        (504, "upstream_timeout"),
        (401, "user_not_found"),
    ],
)
def test_all_proxy_owned_unavailability_envelopes_trip_the_configured_circuit(
    status_code: int,
    code: str,
) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: _preview_proxy_failure_response(status_code, code))
    )
    gateway = ProviderGateway(
        _access("gmail"),
        proxy_infrastructure_failure_threshold=1,
        client=client,
    )

    with pytest.raises(ProviderInfrastructureError) as exc_info:
        _run(gateway, {"provider": "gmail", "method": "GET", "path": "/gmail/v1/users/me/messages"})

    assert exc_info.value.code == code
    assert exc_info.value.status_code == status_code
    asyncio.run(client.aclose())


def test_environment_destroyed_preview_proxy_failure_trips_immediately_after_tracing() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: _preview_proxy_failure_response(410, "environment_destroyed"))
    )
    gateway = ProviderGateway(_access("gmail"), client=client)

    with pytest.raises(ProviderInfrastructureError) as exc_info:
        _run(gateway, {"provider": "gmail", "method": "GET", "path": "/gmail/v1/users/me/messages"})

    assert exc_info.value.code == "environment_destroyed"
    assert exc_info.value.status_code == 410
    assert exc_info.value.consecutive_failures == 1
    assert len(gateway.trace_records) == 1
    assert gateway.trace_records[0].status_code == 410
    asyncio.run(client.aclose())


def test_consecutive_exact_preview_proxy_failures_trip_at_conservative_threshold() -> None:
    responses = [
        (502, "upstream_http_error"),
        (502, "upstream_connect_error"),
        (502, "upstream_connect_timeout"),
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        status_code, code = responses.pop(0)
        return _preview_proxy_failure_response(status_code, code)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access("gmail", "google_calendar"), client=client)

    async def exercise() -> None:
        first = await gateway.execute({"provider": "gmail", "method": "GET", "path": "/gmail/v1/users/me/messages"})
        second = await gateway.execute(
            {
                "provider": "google_calendar",
                "method": "GET",
                "path": "/calendar/v3/users/me/calendarList",
            }
        )
        assert first["status_code"] == 502
        assert second["status_code"] == 502
        with pytest.raises(ProviderInfrastructureError) as exc_info:
            await gateway.execute({"provider": "gmail", "method": "GET", "path": "/gmail/v1/users/me/messages"})
        assert exc_info.value.code == "upstream_connect_timeout"
        assert exc_info.value.consecutive_failures == 3

    asyncio.run(exercise())

    assert len(gateway.trace_records) == 3
    assert [record.status_code for record in gateway.trace_records] == [502, 502, 502]
    asyncio.run(client.aclose())


def test_non_proxy_response_resets_consecutive_preview_proxy_failure_streak() -> None:
    responses = [
        _preview_proxy_failure_response(502, "upstream_connect_error"),
        _preview_proxy_failure_response(504, "upstream_timeout"),
        httpx.Response(503, json={"error": {"code": "provider_temporarily_unavailable"}}),
        _preview_proxy_failure_response(502, "upstream_connect_error"),
        _preview_proxy_failure_response(504, "upstream_timeout"),
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(_access("gmail"), client=client)

    async def exercise() -> list[dict[str, object]]:
        return [
            await gateway.execute({"provider": "gmail", "method": "GET", "path": "/gmail/v1/users/me/messages"})
            for _ in range(5)
        ]

    results = asyncio.run(exercise())

    assert [result["status_code"] for result in results] == [502, 504, 503, 502, 504]
    assert len(gateway.trace_records) == 5
    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    ("body_request_id", "response_request_id"),
    [
        ("request-1", None),
        ("", ""),
        ("request-1", "request-2"),
    ],
)
def test_proxy_failure_requires_matching_nonempty_body_and_response_request_ids(
    body_request_id: str,
    response_request_id: str | None,
) -> None:
    body = _preview_proxy_failure_body("environment_destroyed")
    body["request_id"] = body_request_id
    headers = {"x-arga-preview-proxy-failure": "1"}
    if response_request_id is not None:
        headers["x-request-id"] = response_request_id
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(410, headers=headers, json=body))
    )
    gateway = ProviderGateway(_access("gmail"), client=client)

    result = _run(gateway, {"provider": "gmail", "method": "GET", "path": "/gmail/v1/users/me/messages"})

    assert result["status_code"] == 410
    assert result["body"] == body
    assert len(gateway.trace_records) == 1
    asyncio.run(client.aclose())


def test_matching_proxy_envelope_without_proxy_owned_marker_does_not_trip_circuit() -> None:
    body = _preview_proxy_failure_body("environment_destroyed")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                410,
                headers={"x-request-id": "request-1"},
                json=body,
            )
        )
    )
    gateway = ProviderGateway(_access("gmail"), client=client)

    result = _run(gateway, {"provider": "gmail", "method": "GET", "path": "/gmail/v1/users/me/messages"})

    assert result["status_code"] == 410
    assert result["body"] == body
    assert len(gateway.trace_records) == 1
    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (410, {"code": "environment_destroyed"}),
        (
            410,
            {
                "code": "environment_destroyed",
                "failure": {
                    "code": "environment_destroyed",
                    "phase": "provider",
                    "kind": "gone",
                    "retryable": False,
                },
            },
        ),
        (409, _preview_proxy_failure_body("environment_destroyed")),
        (502, {"code": "upstream_connect_error", "message": "provider-owned error"}),
        (504, {"error": {"code": "upstream_timeout"}}),
    ],
)
def test_arbitrary_http_statuses_and_provider_errors_do_not_trip_circuit(
    status_code: int,
    body: dict[str, object],
) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                status_code,
                headers={"x-request-id": "request-1"},
                json=body,
            )
        )
    )
    gateway = ProviderGateway(_access("gmail"), client=client)

    result = _run(gateway, {"provider": "gmail", "method": "GET", "path": "/gmail/v1/users/me/messages"})

    assert result["status_code"] == status_code
    assert result["body"] == body
    assert len(gateway.trace_records) == 1
    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ProviderGateway({}),
        lambda: ProviderGateway({"github": {"base_url": "file:///etc/passwd"}}),
        lambda: ProviderGateway({"github": {"base_url": "https://user:secret@example.test"}}),
        lambda: ProviderGateway({"github": {"base_url": "https://example.test/api"}}),
        lambda: ProviderGateway(_access("github"), {"code_host": "gitlab"}),
        lambda: ProviderGateway(_access("github"), max_calls=0),
    ],
)
def test_rejects_unsafe_gateway_configuration(factory: Callable[[], ProviderGateway]) -> None:
    with pytest.raises(ProviderGatewayConfigurationError):
        factory()
