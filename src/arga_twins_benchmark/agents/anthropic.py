from __future__ import annotations

import asyncio
import json
import ssl
from time import monotonic
from typing import Any, Literal, cast

import httpx

from arga_twins_benchmark.agents.common import (
    api_error_excerpt,
    elapsed_ms,
    json_tool_output,
    merge_usage,
    parse_tool_arguments,
    result,
)
from arga_twins_benchmark.agents.models import (
    ModelInvocationResult,
    ToolExecutor,
    ToolSchemaInput,
    normalize_tool_schemas,
)
from arga_twins_benchmark.errors import RetryableInfrastructureError

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MAX_OUTPUT_TOKENS = 128_000


def _overlay_mapping(target: dict[str, Any], update: object) -> None:
    if not isinstance(update, dict):
        return
    for key, value in cast(dict[str, object], update).items():
        if isinstance(value, dict):
            nested = target.setdefault(key, {})
            if not isinstance(nested, dict):
                nested = {}
                target[key] = nested
            _overlay_mapping(cast(dict[str, Any], nested), value)
        else:
            target[key] = value


def _sse_payloads(response_text: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    data_lines: list[str] = []

    def flush() -> None:
        if not data_lines:
            return
        raw_data = "\n".join(data_lines)
        data_lines.clear()
        if raw_data == "[DONE]":
            return
        try:
            value: object = json.loads(raw_data)
        except json.JSONDecodeError:
            return
        if isinstance(value, dict):
            payloads.append(cast(dict[str, Any], value))

    for line in response_text.splitlines():
        if not line:
            flush()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").lstrip())
    flush()
    return payloads


def _message_from_sse(response_text: str) -> dict[str, Any] | None:
    message: dict[str, Any] | None = None
    blocks: dict[int, dict[str, Any]] = {}
    partial_json: dict[int, list[str]] = {}
    response_usage: dict[str, Any] = {}

    for event in _sse_payloads(response_text):
        event_type = event.get("type")
        if event_type == "message_start":
            raw_message = event.get("message")
            if not isinstance(raw_message, dict):
                continue
            message = dict(cast(dict[str, Any], raw_message))
            raw_content = message.get("content")
            if isinstance(raw_content, list):
                blocks.update(
                    {
                        index: dict(cast(dict[str, Any], block))
                        for index, block in enumerate(raw_content)
                        if isinstance(block, dict)
                    }
                )
            _overlay_mapping(response_usage, message.get("usage"))
        elif event_type == "content_block_start":
            index = event.get("index")
            raw_block = event.get("content_block")
            if isinstance(index, int) and isinstance(raw_block, dict):
                blocks[index] = dict(cast(dict[str, Any], raw_block))
        elif event_type == "content_block_delta":
            index = event.get("index")
            raw_delta = event.get("delta")
            if not isinstance(index, int) or not isinstance(raw_delta, dict):
                continue
            delta = cast(dict[str, Any], raw_delta)
            block = blocks.setdefault(index, {})
            delta_type = delta.get("type")
            if delta_type == "input_json_delta":
                fragment = delta.get("partial_json")
                if isinstance(fragment, str):
                    partial_json.setdefault(index, []).append(fragment)
            elif delta_type == "text_delta":
                text = delta.get("text")
                if isinstance(text, str):
                    block["text"] = f"{block.get('text', '')}{text}"
            elif delta_type == "thinking_delta":
                thinking = delta.get("thinking")
                if isinstance(thinking, str):
                    block["thinking"] = f"{block.get('thinking', '')}{thinking}"
            elif delta_type == "signature_delta":
                signature = delta.get("signature")
                if isinstance(signature, str):
                    block["signature"] = f"{block.get('signature', '')}{signature}"
            elif delta_type == "citations_delta":
                citation = delta.get("citation")
                if isinstance(citation, dict):
                    block.setdefault("citations", []).append(citation)
        elif event_type == "content_block_stop":
            index = event.get("index")
            if not isinstance(index, int) or index not in partial_json:
                continue
            raw_input = "".join(partial_json.pop(index))
            try:
                parsed_input: object = json.loads(raw_input)
            except json.JSONDecodeError:
                parsed_input = raw_input
            blocks.setdefault(index, {})["input"] = parsed_input
        elif event_type == "message_delta":
            if message is None:
                message = {}
            raw_delta = event.get("delta")
            if isinstance(raw_delta, dict):
                message.update(cast(dict[str, Any], raw_delta))
            _overlay_mapping(response_usage, event.get("usage"))

    if message is None:
        return None
    for index, fragments in partial_json.items():
        raw_input = "".join(fragments)
        try:
            parsed_input = json.loads(raw_input)
        except json.JSONDecodeError:
            parsed_input = raw_input
        blocks.setdefault(index, {})["input"] = parsed_input
    message["content"] = [blocks[index] for index in sorted(blocks)]
    message["usage"] = response_usage
    return message


class AnthropicMessagesAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        model_id: Literal["claude-opus-4-8", "claude-fable-5"],
        client: httpx.AsyncClient | None = None,
        endpoint: str = ANTHROPIC_MESSAGES_URL,
    ) -> None:
        if not api_key:
            raise ValueError("Anthropic API key cannot be empty")
        self.api_key = api_key
        self.model_id = model_id
        self.endpoint = endpoint
        self._client = client

    def _thinking_config(self) -> dict[str, Any] | None:
        if self.model_id == "claude-opus-4-8":
            return {"type": "adaptive"}
        return None

    def _config(self, *, max_tool_calls: int, timeout_seconds: float) -> dict[str, Any]:
        return {
            "model": self.model_id,
            "provider": "anthropic",
            "endpoint": "messages",
            "thinking": self._thinking_config() or "model_default_always",
            "effort": "high",
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "temperature": None,
            "max_tool_calls": max_tool_calls,
            "timeout_seconds": timeout_seconds,
            "fallback": None,
        }

    async def invoke(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tool_schema: ToolSchemaInput,
        execute_tool: ToolExecutor,
        max_tool_calls: int,
        timeout_seconds: float,
    ) -> ModelInvocationResult:
        if max_tool_calls < 0:
            raise ValueError("max_tool_calls must be non-negative")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        tools = normalize_tool_schemas(tool_schema)
        tool_names = {tool.name for tool in tools}
        tool_payload = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in tools
        ]
        config = self._config(max_tool_calls=max_tool_calls, timeout_seconds=timeout_seconds)
        events: list[dict[str, Any]] = [
            {
                "type": "invocation_started",
                "provider": "anthropic",
                "requested_model": self.model_id,
                "config": config,
            }
        ]
        usage: dict[str, Any] = {}
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
        response_model: str | None = None
        tool_calls = 0
        started = monotonic()
        client = self._client or httpx.AsyncClient(timeout=None)
        owns_client = self._client is None

        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    request_body: dict[str, Any] = {
                        "model": self.model_id,
                        "max_tokens": MAX_OUTPUT_TOKENS,
                        "stream": True,
                        "system": system_prompt,
                        "messages": messages,
                        "tools": tool_payload,
                        "output_config": {"effort": "high"},
                    }
                    thinking_config = self._thinking_config()
                    if thinking_config is not None:
                        request_body["thinking"] = thinking_config
                    response: httpx.Response | None = None
                    for transport_attempt in range(1, 4):
                        try:
                            response = await client.post(
                                self.endpoint,
                                headers={
                                    "anthropic-version": ANTHROPIC_VERSION,
                                    "content-type": "application/json",
                                    "x-api-key": self.api_key,
                                },
                                json=request_body,
                            )
                            break
                        except (httpx.TransportError, ssl.SSLError) as error:
                            events.append(
                                {
                                    "type": "transport_error",
                                    "error_type": type(error).__name__,
                                    "attempt": transport_attempt,
                                    "will_retry": transport_attempt < 3,
                                }
                            )
                            if transport_attempt == 3:
                                raise
                            await asyncio.sleep(transport_attempt)
                    if response is None:  # pragma: no cover - defensive exhaustiveness
                        raise RuntimeError("Anthropic transport loop returned no response")
                    if response.status_code >= 400:
                        events.append(
                            {
                                "type": "api_error",
                                "status_code": response.status_code,
                                "body": api_error_excerpt(response.text),
                            }
                        )
                        return result(
                            requested_model=self.model_id,
                            response_model=response_model,
                            provider="anthropic",
                            final_text="",
                            status="api_error",
                            stop_reason=f"http_{response.status_code}",
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            events=events,
                            usage=usage,
                            config=config,
                            started=started,
                            tool_calls=tool_calls,
                        )
                    if response.headers.get("content-type", "").startswith("text/event-stream"):
                        payload: object = _message_from_sse(response.text)
                    else:
                        try:
                            payload = response.json()
                        except ValueError:
                            payload = None
                    if not isinstance(payload, dict):
                        events.append({"type": "invalid_response", "reason": "response_not_object"})
                        return result(
                            requested_model=self.model_id,
                            response_model=response_model,
                            provider="anthropic",
                            final_text="",
                            status="invalid_response",
                            stop_reason="response_not_object",
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            events=events,
                            usage=usage,
                            config=config,
                            started=started,
                            tool_calls=tool_calls,
                        )

                    message = cast(dict[str, Any], payload)
                    raw_model = message.get("model")
                    if isinstance(raw_model, str):
                        response_model = raw_model
                    stop_reason = message.get("stop_reason")
                    content_value = message.get("content")
                    response_usage = message.get("usage")
                    merge_usage(usage, response_usage)
                    if not isinstance(stop_reason, str) or not isinstance(content_value, list):
                        events.append({"type": "invalid_response", "reason": "missing_stop_reason_or_content"})
                        return result(
                            requested_model=self.model_id,
                            response_model=response_model,
                            provider="anthropic",
                            final_text="",
                            status="invalid_response",
                            stop_reason="missing_stop_reason_or_content",
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            events=events,
                            usage=usage,
                            config=config,
                            started=started,
                            tool_calls=tool_calls,
                        )
                    content = cast(list[object], content_value)
                    events.append(
                        {
                            "type": "assistant_response",
                            "response_model": response_model,
                            "stop_reason": stop_reason,
                            "content": content,
                            "usage": response_usage,
                        }
                    )
                    messages.append({"role": "assistant", "content": content})
                    text_blocks: list[object] = []
                    for block_value in content:
                        if not isinstance(block_value, dict):
                            continue
                        block = cast(dict[str, object], block_value)
                        if block.get("type") == "text":
                            text_blocks.append(block.get("text", ""))
                    final_text = "\n".join(text for text in text_blocks if isinstance(text, str)).strip()

                    if stop_reason == "tool_use":
                        raw_calls: list[dict[str, object]] = []
                        for block_value in content:
                            if not isinstance(block_value, dict):
                                continue
                            block = cast(dict[str, object], block_value)
                            if block.get("type") == "tool_use":
                                raw_calls.append(block)
                        if not raw_calls:
                            events.append({"type": "invalid_response", "reason": "tool_use_without_calls"})
                            return result(
                                requested_model=self.model_id,
                                response_model=response_model,
                                provider="anthropic",
                                final_text=final_text,
                                status="invalid_response",
                                stop_reason="tool_use_without_calls",
                                system_prompt=system_prompt,
                                user_prompt=user_prompt,
                                events=events,
                                usage=usage,
                                config=config,
                                started=started,
                                tool_calls=tool_calls,
                            )
                        if tool_calls + len(raw_calls) > max_tool_calls:
                            events.append(
                                {
                                    "type": "tool_limit_exceeded",
                                    "attempted_calls": len(raw_calls),
                                    "completed_calls": tool_calls,
                                    "max_tool_calls": max_tool_calls,
                                }
                            )
                            return result(
                                requested_model=self.model_id,
                                response_model=response_model,
                                provider="anthropic",
                                final_text=final_text,
                                status="tool_limit_exceeded",
                                stop_reason="tool_limit_exceeded",
                                system_prompt=system_prompt,
                                user_prompt=user_prompt,
                                events=events,
                                usage=usage,
                                config=config,
                                started=started,
                                tool_calls=tool_calls,
                            )

                        tool_results: list[dict[str, Any]] = []
                        for raw_call in raw_calls:
                            call_id = raw_call.get("id")
                            name = raw_call.get("name")
                            arguments, invalid_arguments = parse_tool_arguments(raw_call.get("input"))
                            call_started = monotonic()
                            is_error = invalid_arguments or not isinstance(call_id, str) or name not in tool_names
                            if is_error:
                                tool_output: object = {"error": {"type": "InvalidToolCall"}}
                            else:
                                try:
                                    tool_output = await execute_tool(cast(str, name), arguments)
                                except asyncio.CancelledError:
                                    raise
                                except RetryableInfrastructureError:
                                    raise
                                except Exception as error:
                                    tool_output = {
                                        "error": {
                                            "type": "ToolExecutionError",
                                            "exception_type": type(error).__name__,
                                        }
                                    }
                                    is_error = True
                            tool_calls += 1
                            events.append(
                                {
                                    "type": "tool_call",
                                    "provider_call_index": tool_calls,
                                    "tool_use_id": call_id,
                                    "name": name,
                                    "arguments": arguments,
                                    "output": tool_output,
                                    "is_error": is_error,
                                    "latency_ms": elapsed_ms(call_started),
                                }
                            )
                            result_block: dict[str, Any] = {
                                "type": "tool_result",
                                "tool_use_id": call_id if isinstance(call_id, str) else "invalid-tool-use-id",
                                "content": json_tool_output(tool_output),
                            }
                            if is_error:
                                result_block["is_error"] = True
                            tool_results.append(result_block)
                        messages.append({"role": "user", "content": tool_results})
                        continue

                    if stop_reason == "pause_turn":
                        continue
                    if stop_reason == "refusal":
                        return result(
                            requested_model=self.model_id,
                            response_model=response_model,
                            provider="anthropic",
                            final_text=final_text,
                            status="refused",
                            stop_reason=stop_reason,
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            events=events,
                            usage=usage,
                            config=config,
                            started=started,
                            tool_calls=tool_calls,
                        )
                    if stop_reason == "end_turn":
                        return result(
                            requested_model=self.model_id,
                            response_model=response_model,
                            provider="anthropic",
                            final_text=final_text,
                            status="completed",
                            stop_reason=stop_reason,
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            events=events,
                            usage=usage,
                            config=config,
                            started=started,
                            tool_calls=tool_calls,
                        )
                    return result(
                        requested_model=self.model_id,
                        response_model=response_model,
                        provider="anthropic",
                        final_text=final_text,
                        status="incomplete",
                        stop_reason=stop_reason,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        events=events,
                        usage=usage,
                        config=config,
                        started=started,
                        tool_calls=tool_calls,
                    )
        except TimeoutError:
            events.append({"type": "timed_out", "timeout_seconds": timeout_seconds})
            return result(
                requested_model=self.model_id,
                response_model=response_model,
                provider="anthropic",
                final_text="",
                status="timed_out",
                stop_reason="timeout",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                events=events,
                usage=usage,
                config=config,
                started=started,
                tool_calls=tool_calls,
            )
        except httpx.HTTPError as error:
            events.append(
                {
                    "type": "api_error",
                    "reason": "transport_error",
                    "exception_type": type(error).__name__,
                }
            )
            return result(
                requested_model=self.model_id,
                response_model=response_model,
                provider="anthropic",
                final_text="",
                status="api_error",
                stop_reason="transport_error",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                events=events,
                usage=usage,
                config=config,
                started=started,
                tool_calls=tool_calls,
            )
        finally:
            if owns_client:
                await client.aclose()
