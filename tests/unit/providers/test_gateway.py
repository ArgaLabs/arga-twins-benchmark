from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast

import httpx
import pytest

from arga_twins_benchmark.providers import ProviderGateway, ProviderGatewayConfigurationError


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

    result = _run(
        gateway,
        {
            "provider": "issue_tracker",
            "method": "POST",
            "path": "/graphql?z=9",
            "query": {"z": ["2", "1"], "a": "hello world"},
            "body": body,
        },
    )

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
    asyncio.run(client.aclose())


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


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ProviderGateway({}),
        lambda: ProviderGateway({"github": {"base_url": "file:///etc/passwd"}}),
        lambda: ProviderGateway({"github": {"base_url": "https://user:secret@example.test"}}),
        lambda: ProviderGateway({"github": {"base_url": "https://example.test/api"}}),
        lambda: ProviderGateway(_access("github"), {"code_host": "gitlab"}),
    ],
)
def test_rejects_unsafe_gateway_configuration(factory: Callable[[], ProviderGateway]) -> None:
    with pytest.raises(ProviderGatewayConfigurationError):
        factory()
