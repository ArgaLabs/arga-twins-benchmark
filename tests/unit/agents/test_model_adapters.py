from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from arga_twins_benchmark.agents.anthropic import AnthropicMessagesAdapter
from arga_twins_benchmark.agents.google import GoogleGenerateContentAdapter
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


def anthropic_sse(*events: dict[str, Any]) -> str:
    return (
        "\n\n".join(f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}" for event in events)
        + "\n\n"
    )


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
            headers={"Content-Type": "text/event-stream"},
            text=anthropic_sse(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_fable",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-fable-5",
                        "content": [],
                        "stop_reason": None,
                        "usage": {"input_tokens": 10, "output_tokens": 1},
                    },
                },
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "I cannot do that."},
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "refusal", "stop_sequence": None},
                    "usage": {"output_tokens": 2},
                },
                {"type": "message_stop"},
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
    assert seen_request["stream"] is True
    assert seen_request["max_tokens"] == 128_000
    assert seen_request["output_config"] == {"effort": "high"}
    assert result.config["thinking"] == "model_default_always"
    assert result.config["max_output_tokens"] == 128_000
    assert result.usage == {"input_tokens": 10, "output_tokens": 2}


def test_anthropic_stream_reconstructs_thinking_and_tool_input() -> None:
    request_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        start = {
            "type": "message_start",
            "message": {
                "id": f"msg_{request_count}",
                "type": "message",
                "role": "assistant",
                "model": "claude-fable-5",
                "content": [],
                "stop_reason": None,
                "usage": {"input_tokens": 10 * request_count, "output_tokens": 1},
            },
        }
        if request_count == 1:
            events = (
                start,
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "thinking", "thinking": "", "signature": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "Check the provider."},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "signature_delta", "signature": "signed"},
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tool-stream",
                        "name": "provider_api",
                        "input": {},
                    },
                },
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": '{"method":"GET",'},
                },
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": '"path":"/records"}'},
                },
                {"type": "content_block_stop", "index": 1},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                    "usage": {"output_tokens": 8},
                },
                {"type": "message_stop"},
            )
        else:
            events = (
                start,
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Done"},
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 3},
                },
                {"type": "message_stop"},
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text=anthropic_sse(*events),
        )

    calls: list[tuple[str, dict[str, Any]]] = []

    async def execute_tool(name: str, arguments: dict[str, Any]) -> object:
        calls.append((name, arguments))
        return {"status_code": 200, "body": []}

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

    assert result.status == "completed"
    assert result.final_text == "Done"
    assert result.usage == {"input_tokens": 30, "output_tokens": 11}
    assert calls == [("provider_api", {"method": "GET", "path": "/records"})]


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


@pytest.mark.parametrize(
    ("model_id", "effort"),
    [
        ("claude-fable-5", "xhigh"),
        ("claude-opus-5", "max"),
        ("claude-opus-4-8", "medium"),
        ("claude-sonnet-5", "low"),
    ],
)
def test_anthropic_sends_the_exact_requested_effort(model_id: str, effort: str) -> None:
    request_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json=anthropic_message(
                model=model_id,
                content=[{"type": "text", "text": "done"}],
                stop_reason="end_turn",
            ),
        )

    async def execute_tool(_name: str, _arguments: dict[str, Any]) -> object:
        raise AssertionError("tool must not be called")

    client = async_client(handler)
    adapter = AnthropicMessagesAdapter(
        api_key="test-anthropic-key",
        model_id=model_id,  # type: ignore[arg-type]
        effort=effort,  # type: ignore[arg-type]
        thinking_mode="adaptive",
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

    assert result.status == "completed"
    assert request_body["output_config"] == {"effort": effort}
    assert request_body["thinking"] == {"type": "adaptive"}
    assert result.config["effort"] == effort


@pytest.mark.parametrize("model_id", ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"])
def test_openai_sends_native_max_effort_for_each_requested_model(model_id: str) -> None:
    request_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json=openai_response(
                model=model_id,
                output=[
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ],
            ),
        )

    async def execute_tool(_name: str, _arguments: dict[str, Any]) -> object:
        raise AssertionError("tool must not be called")

    client = async_client(handler)
    adapter = OpenAIResponsesAdapter(
        api_key="test-openai-key",
        model_id=model_id,  # type: ignore[arg-type]
        effort="max",
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

    assert result.status == "completed"
    assert request_body["model"] == model_id
    assert request_body["reasoning"] == {"effort": "max"}
    assert result.config["reasoning"] == {"effort": "max"}


def test_google_preserves_thought_signatures_and_groups_parallel_tool_results() -> None:
    requests: list[dict[str, Any]] = []
    signed_content = {
        "role": "model",
        "parts": [
            {"thought": True, "text": "private reasoning", "thoughtSignature": "signed-thought"},
            {
                "functionCall": {
                    "id": "call-1",
                    "name": "provider_api",
                    "args": {"method": "GET", "path": "/records"},
                }
            },
            {
                "functionCall": {
                    "id": "call-2",
                    "name": "provider_api",
                    "args": {"method": "GET", "path": "/other"},
                }
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "candidates": [{"content": signed_content, "finishReason": "STOP"}],
                    "usageMetadata": {
                        "promptTokenCount": 10,
                        "candidatesTokenCount": 2,
                        "thoughtsTokenCount": 3,
                        "totalTokenCount": 15,
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": '{"status":"done"}'}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 20,
                    "candidatesTokenCount": 4,
                    "thoughtsTokenCount": 1,
                    "totalTokenCount": 25,
                },
            },
        )

    calls: list[tuple[str, dict[str, Any]]] = []

    async def execute_tool(name: str, arguments: dict[str, Any]) -> object:
        calls.append((name, arguments))
        return {"status_code": 200, "body": []}

    client = async_client(handler)
    adapter = GoogleGenerateContentAdapter(
        api_key="test-google-key",
        model_id="gemini-3.5-flash",
        client=client,
        endpoint="https://google.test/v1beta/models",
    )
    result = asyncio.run(
        adapter.invoke(
            system_prompt="SYSTEM EXACT",
            user_prompt="USER EXACT",
            tool_schema=TOOL_SCHEMA,
            execute_tool=execute_tool,
            max_tool_calls=4,
            timeout_seconds=10,
        )
    )
    asyncio.run(client.aclose())

    assert result.status == "completed"
    assert result.final_text == '{"status":"done"}'
    assert result.tool_calls == 2
    assert result.usage["input_tokens"] == 30
    assert result.usage["output_tokens"] == 10
    assert requests[0]["systemInstruction"] == {"parts": [{"text": "SYSTEM EXACT"}]}
    assert requests[0]["generationConfig"] == {"maxOutputTokens": 65_536}
    assert "thinkingConfig" not in requests[0]["generationConfig"]
    assert requests[1]["contents"][1] == signed_content
    response_parts = requests[1]["contents"][2]["parts"]
    assert [part["functionResponse"]["id"] for part in response_parts] == ["call-1", "call-2"]
    assert [part["functionResponse"]["name"] for part in response_parts] == [
        "provider_api",
        "provider_api",
    ]
    assert calls == [
        ("provider_api", {"method": "GET", "path": "/records"}),
        ("provider_api", {"method": "GET", "path": "/other"}),
    ]
