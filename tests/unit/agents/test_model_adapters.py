from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from arga_twins_benchmark.agents.anthropic import AnthropicMessagesAdapter
from arga_twins_benchmark.agents.openai import OpenAIResponsesAdapter
from arga_twins_benchmark.agents.runner import invoke_model
from arga_twins_benchmark.providers import ProviderInfrastructureError

TOOL_SCHEMA = {
    "name": "provider_api",
    "description": "Call a provisioned provider.",
    "input_schema": {
        "type": "object",
        "properties": {
            "method": {"type": "string"},
            "path": {"type": "string"},
        },
        "required": ["method", "path"],
        "additionalProperties": False,
    },
}


def async_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def anthropic_message(
    *,
    content: list[dict[str, Any]],
    stop_reason: str,
    model: str = "claude-opus-4-8-20260701",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "usage": usage or {"input_tokens": 10, "output_tokens": 2},
    }


def openai_response(
    *,
    output: list[dict[str, Any]],
    status: str = "completed",
    model: str = "gpt-5.6-sol-2026-07-01",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "resp_test",
        "object": "response",
        "status": status,
        "model": model,
        "output": output,
        "usage": usage
        or {
            "input_tokens": 12,
            "output_tokens": 3,
            "total_tokens": 15,
            "output_tokens_details": {"reasoning_tokens": 1},
        },
    }


def test_anthropic_opus_runs_tool_loop_with_exact_prompts_and_high_effort() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=anthropic_message(
                    content=[
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "provider_api",
                            "input": {"method": "GET", "path": "/records"},
                        }
                    ],
                    stop_reason="tool_use",
                    usage={"input_tokens": 10, "output_tokens": 2},
                ),
            )
        return httpx.Response(
            200,
            json=anthropic_message(
                content=[{"type": "text", "text": '{"decision":"done"}'}],
                stop_reason="end_turn",
                usage={"input_tokens": 20, "output_tokens": 4},
            ),
        )

    tool_inputs: list[tuple[str, dict[str, Any]]] = []

    async def execute_tool(name: str, arguments: dict[str, Any]) -> object:
        tool_inputs.append((name, arguments))
        return {"status_code": 200, "body": [{"id": "r1"}]}

    client = async_client(handler)
    adapter = AnthropicMessagesAdapter(
        api_key="test-anthropic-key",
        model_id="claude-opus-4-8",
        client=client,
        endpoint="https://anthropic.test/v1/messages",
    )
    result = asyncio.run(
        adapter.invoke(
            system_prompt="SYSTEM EXACT",
            user_prompt="USER EXACT",
            tool_schema=TOOL_SCHEMA,
            execute_tool=execute_tool,
            max_tool_calls=5,
            timeout_seconds=10,
        )
    )
    asyncio.run(client.aclose())

    assert result.status == "completed"
    assert result.final_text == '{"decision":"done"}'
    assert result.system_prompt == "SYSTEM EXACT"
    assert result.user_prompt == "USER EXACT"
    assert result.response_model == "claude-opus-4-8-20260701"
    assert result.tool_calls == 1
    assert result.usage == {"input_tokens": 30, "output_tokens": 6}
    assert result.config["thinking"] == {"type": "adaptive"}
    assert result.config["effort"] == "high"
    assert result.config["temperature"] is None
    assert result.config["fallback"] is None
    assert tool_inputs == [("provider_api", {"method": "GET", "path": "/records"})]
    assert requests[0]["system"] == "SYSTEM EXACT"
    assert requests[0]["messages"] == [{"role": "user", "content": "USER EXACT"}]
    assert requests[0]["thinking"] == {"type": "adaptive"}
    assert requests[0]["output_config"] == {"effort": "high"}
    assert "temperature" not in requests[0]
    assert requests[1]["messages"][-1]["content"][0]["type"] == "tool_result"


def test_anthropic_fable_uses_model_default_thinking_and_handles_refusal() -> None:
    seen_request: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_request.update(json.loads(request.content))
        return httpx.Response(
            200,
            json=anthropic_message(
                content=[{"type": "text", "text": "I cannot do that."}],
                stop_reason="refusal",
                model="claude-fable-5",
            ),
        )

    async def execute_tool(_name: str, _arguments: dict[str, Any]) -> object:
        raise AssertionError("tool must not be called")

    client = async_client(handler)
    adapter = AnthropicMessagesAdapter(
        api_key="test-anthropic-key",
        model_id="claude-fable-5",
        client=client,
        endpoint="https://anthropic.test/v1/messages",
    )
    result = asyncio.run(
        adapter.invoke(
            system_prompt="system",
            user_prompt="user",
            tool_schema=TOOL_SCHEMA,
            execute_tool=execute_tool,
            max_tool_calls=2,
            timeout_seconds=10,
        )
    )
    asyncio.run(client.aclose())

    assert result.status == "refused"
    assert result.stop_reason == "refusal"
    assert result.final_text == "I cannot do that."
    assert "thinking" not in seen_request
    assert seen_request["output_config"] == {"effort": "high"}
    assert result.config["thinking"] == "model_default_always"


def test_anthropic_tool_limit_preflights_batch_without_side_effects() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=anthropic_message(
                content=[
                    {"type": "tool_use", "id": "tool-1", "name": "provider_api", "input": {"path": "/one"}},
                    {"type": "tool_use", "id": "tool-2", "name": "provider_api", "input": {"path": "/two"}},
                ],
                stop_reason="tool_use",
            ),
        )

    executed = False

    async def execute_tool(_name: str, _arguments: dict[str, Any]) -> object:
        nonlocal executed
        executed = True
        return {}

    client = async_client(handler)
    adapter = AnthropicMessagesAdapter(
        api_key="test-anthropic-key",
        model_id="claude-opus-4-8",
        client=client,
    )
    result = asyncio.run(
        adapter.invoke(
            system_prompt="system",
            user_prompt="user",
            tool_schema=TOOL_SCHEMA,
            execute_tool=execute_tool,
            max_tool_calls=1,
            timeout_seconds=10,
        )
    )
    asyncio.run(client.aclose())

    assert result.status == "tool_limit_exceeded"
    assert result.tool_calls == 0
    assert executed is False


def test_anthropic_timeout_cancels_slow_tool() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=anthropic_message(
                content=[{"type": "tool_use", "id": "tool-1", "name": "provider_api", "input": {"path": "/slow"}}],
                stop_reason="tool_use",
            ),
        )

    async def execute_tool(_name: str, _arguments: dict[str, Any]) -> object:
        await asyncio.sleep(1)
        return {}

    client = async_client(handler)
    adapter = AnthropicMessagesAdapter(
        api_key="test-anthropic-key",
        model_id="claude-opus-4-8",
        client=client,
    )
    result = asyncio.run(
        adapter.invoke(
            system_prompt="system",
            user_prompt="user",
            tool_schema=TOOL_SCHEMA,
            execute_tool=execute_tool,
            max_tool_calls=1,
            timeout_seconds=0.01,
        )
    )
    asyncio.run(client.aclose())

    assert result.status == "timed_out"
    assert result.stop_reason == "timeout"
    assert result.tool_calls == 0


def test_anthropic_propagates_retryable_provider_infrastructure_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=anthropic_message(
                content=[
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "provider_api",
                        "input": {"method": "GET", "path": "/records"},
                    }
                ],
                stop_reason="tool_use",
            ),
        )

    async def execute_tool(_name: str, _arguments: dict[str, Any]) -> object:
        raise ProviderInfrastructureError(
            code="environment_destroyed",
            status_code=410,
            consecutive_failures=1,
        )

    client = async_client(handler)
    adapter = AnthropicMessagesAdapter(
        api_key="test-anthropic-key",
        model_id="claude-opus-4-8",
        client=client,
    )

    with pytest.raises(ProviderInfrastructureError, match="environment_destroyed"):
        asyncio.run(
            adapter.invoke(
                system_prompt="system",
                user_prompt="user",
                tool_schema=TOOL_SCHEMA,
                execute_tool=execute_tool,
                max_tool_calls=1,
                timeout_seconds=10,
            )
        )

    asyncio.run(client.aclose())


def test_openai_responses_runs_function_loop_and_replays_output_items() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=openai_response(
                    output=[
                        {
                            "id": "rs_1",
                            "type": "reasoning",
                            "encrypted_content": "opaque",
                            "summary": [],
                        },
                        {
                            "id": "fc_1",
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "provider_api",
                            "arguments": '{"method":"GET","path":"/records"}',
                        },
                    ],
                    usage={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
                ),
            )
        return httpx.Response(
            200,
            json=openai_response(
                output=[
                    {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": '{"decision":"done"}', "annotations": []}],
                    }
                ],
                usage={
                    "input_tokens": 22,
                    "output_tokens": 5,
                    "total_tokens": 27,
                    "output_tokens_details": {"reasoning_tokens": 2},
                },
            ),
        )

    async def execute_tool(name: str, arguments: dict[str, Any]) -> object:
        assert name == "provider_api"
        assert arguments == {"method": "GET", "path": "/records"}
        return {"status_code": 200, "body": []}

    client = async_client(handler)
    adapter = OpenAIResponsesAdapter(
        api_key="test-openai-key",
        client=client,
        endpoint="https://openai.test/v1/responses",
    )
    result = asyncio.run(
        adapter.invoke(
            system_prompt="SYSTEM EXACT",
            user_prompt="USER EXACT",
            tool_schema=TOOL_SCHEMA,
            execute_tool=execute_tool,
            max_tool_calls=5,
            timeout_seconds=10,
        )
    )
    asyncio.run(client.aclose())

    assert result.status == "completed"
    assert result.final_text == '{"decision":"done"}'
    assert result.response_model == "gpt-5.6-sol-2026-07-01"
    assert result.tool_calls == 1
    assert result.usage == {
        "input_tokens": 34,
        "output_tokens": 8,
        "total_tokens": 42,
        "output_tokens_details": {"reasoning_tokens": 2},
    }
    assert result.config["reasoning"] == {"effort": "high"}
    assert result.config["temperature"] is None
    assert result.config["fallback"] is None
    assert requests[0]["instructions"] == "SYSTEM EXACT"
    assert requests[0]["input"] == [{"role": "user", "content": "USER EXACT"}]
    assert requests[0]["reasoning"] == {"effort": "high"}
    assert requests[0]["store"] is False
    assert requests[0]["include"] == ["reasoning.encrypted_content"]
    assert "temperature" not in requests[0]
    assert any(item.get("type") == "reasoning" for item in requests[1]["input"])
    assert any(item.get("type") == "function_call" for item in requests[1]["input"])
    assert any(item.get("type") == "function_call_output" for item in requests[1]["input"])


def test_openai_propagates_retryable_provider_infrastructure_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=openai_response(
                output=[
                    {
                        "id": "fc_1",
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "provider_api",
                        "arguments": '{"method":"GET","path":"/records"}',
                    }
                ]
            ),
        )

    async def execute_tool(_name: str, _arguments: dict[str, Any]) -> object:
        raise ProviderInfrastructureError(
            code="upstream_timeout",
            status_code=504,
            consecutive_failures=3,
        )

    client = async_client(handler)
    adapter = OpenAIResponsesAdapter(api_key="test-openai-key", client=client)

    with pytest.raises(ProviderInfrastructureError, match="upstream_timeout"):
        asyncio.run(
            adapter.invoke(
                system_prompt="system",
                user_prompt="user",
                tool_schema=TOOL_SCHEMA,
                execute_tool=execute_tool,
                max_tool_calls=1,
                timeout_seconds=10,
            )
        )

    asyncio.run(client.aclose())


def test_openai_responses_handles_refusal() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=openai_response(
                output=[
                    {
                        "id": "msg_refusal",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "refusal", "refusal": "I cannot do that."}],
                    }
                ]
            ),
        )

    async def execute_tool(_name: str, _arguments: dict[str, Any]) -> object:
        raise AssertionError("tool must not be called")

    client = async_client(handler)
    adapter = OpenAIResponsesAdapter(api_key="test-openai-key", client=client)
    result = asyncio.run(
        adapter.invoke(
            system_prompt="system",
            user_prompt="user",
            tool_schema=TOOL_SCHEMA,
            execute_tool=execute_tool,
            max_tool_calls=1,
            timeout_seconds=10,
        )
    )
    asyncio.run(client.aclose())

    assert result.status == "refused"
    assert result.stop_reason == "refusal"
    assert result.final_text == "I cannot do that."


def test_api_error_is_structured_and_does_not_fallback() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    async def execute_tool(_name: str, _arguments: dict[str, Any]) -> object:
        raise AssertionError("tool must not be called")

    client = async_client(handler)
    adapter = OpenAIResponsesAdapter(api_key="test-openai-key", client=client)
    result = asyncio.run(
        adapter.invoke(
            system_prompt="system",
            user_prompt="user",
            tool_schema=TOOL_SCHEMA,
            execute_tool=execute_tool,
            max_tool_calls=1,
            timeout_seconds=10,
        )
    )
    asyncio.run(client.aclose())

    assert result.status == "api_error"
    assert result.stop_reason == "http_429"
    assert result.config["fallback"] is None
    assert calls == 1


def test_transport_error_is_structured_and_does_not_expose_exception_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-bearing diagnostic", request=request)

    async def execute_tool(_name: str, _arguments: dict[str, Any]) -> object:
        raise AssertionError("tool must not be called")

    client = async_client(handler)
    adapter = AnthropicMessagesAdapter(
        api_key="test-anthropic-key",
        model_id="claude-opus-4-8",
        client=client,
    )
    result = asyncio.run(
        adapter.invoke(
            system_prompt="system",
            user_prompt="user",
            tool_schema=TOOL_SCHEMA,
            execute_tool=execute_tool,
            max_tool_calls=1,
            timeout_seconds=10,
        )
    )
    asyncio.run(client.aclose())

    assert result.status == "api_error"
    assert result.stop_reason == "transport_error"
    assert result.events[-1]["exception_type"] == "ConnectError"
    assert "secret-bearing" not in json.dumps(result.as_dict())


def test_invoke_model_rejects_unknown_model_without_fallback() -> None:
    async def execute_tool(_name: str, _arguments: dict[str, Any]) -> object:
        return {}

    with pytest.raises(ValueError, match="unsupported model"):
        asyncio.run(
            invoke_model(
                "some-other-model",
                "system",
                "user",
                TOOL_SCHEMA,
                execute_tool,
                1,
                10,
            )
        )


def test_invoke_model_requires_the_selected_providers_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    async def execute_tool(_name: str, _arguments: dict[str, Any]) -> object:
        return {}

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        asyncio.run(
            invoke_model(
                "claude-opus-4-8",
                "system",
                "user",
                TOOL_SCHEMA,
                execute_tool,
                1,
                10,
            )
        )
