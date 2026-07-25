from __future__ import annotations

import asyncio
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

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
MAX_OUTPUT_TOKENS = 16_384


class OpenAIResponsesAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        model_id: Literal["gpt-5.6-sol"] = "gpt-5.6-sol",
        client: httpx.AsyncClient | None = None,
        endpoint: str = OPENAI_RESPONSES_URL,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key cannot be empty")
        self.api_key = api_key
        self.model_id = model_id
        self.endpoint = endpoint
        self._client = client

    def _config(self, *, max_tool_calls: int, timeout_seconds: float) -> dict[str, Any]:
        return {
            "model": self.model_id,
            "provider": "openai",
            "endpoint": "responses",
            "reasoning": {"effort": "high"},
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "temperature": None,
            "max_tool_calls": max_tool_calls,
            "timeout_seconds": timeout_seconds,
            "fallback": None,
            "store": False,
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
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            }
            for tool in tools
        ]
        config = self._config(max_tool_calls=max_tool_calls, timeout_seconds=timeout_seconds)
        events: list[dict[str, Any]] = [
            {
                "type": "invocation_started",
                "provider": "openai",
                "requested_model": self.model_id,
                "config": config,
            }
        ]
        usage: dict[str, Any] = {}
        input_items: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
        response_model: str | None = None
        tool_calls = 0
        started = monotonic()
        client = self._client or httpx.AsyncClient(timeout=None)
        owns_client = self._client is None

        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    response = await client.post(
                        self.endpoint,
                        headers={
                            "authorization": f"Bearer {self.api_key}",
                            "content-type": "application/json",
                        },
                        json={
                            "model": self.model_id,
                            "instructions": system_prompt,
                            "input": input_items,
                            "tools": tool_payload,
                            "reasoning": {"effort": "high"},
                            "max_output_tokens": MAX_OUTPUT_TOKENS,
                            "store": False,
                            "include": ["reasoning.encrypted_content"],
                        },
                    )
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
                            provider="openai",
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
                    try:
                        payload: object = response.json()
                    except ValueError:
                        payload = None
                    if not isinstance(payload, dict):
                        events.append({"type": "invalid_response", "reason": "response_not_object"})
                        return result(
                            requested_model=self.model_id,
                            response_model=response_model,
                            provider="openai",
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

                    response_value = cast(dict[str, Any], payload)
                    raw_model = response_value.get("model")
                    if isinstance(raw_model, str):
                        response_model = raw_model
                    response_status = response_value.get("status")
                    output_value = response_value.get("output")
                    response_usage = response_value.get("usage")
                    merge_usage(usage, response_usage)
                    if not isinstance(response_status, str) or not isinstance(output_value, list):
                        events.append({"type": "invalid_response", "reason": "missing_status_or_output"})
                        return result(
                            requested_model=self.model_id,
                            response_model=response_model,
                            provider="openai",
                            final_text="",
                            status="invalid_response",
                            stop_reason="missing_status_or_output",
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            events=events,
                            usage=usage,
                            config=config,
                            started=started,
                            tool_calls=tool_calls,
                        )
                    output = cast(list[object], output_value)
                    events.append(
                        {
                            "type": "assistant_response",
                            "response_model": response_model,
                            "status": response_status,
                            "output": output,
                            "usage": response_usage,
                            "incomplete_details": response_value.get("incomplete_details"),
                            "error": response_value.get("error"),
                        }
                    )

                    if response_status != "completed":
                        return result(
                            requested_model=self.model_id,
                            response_model=response_model,
                            provider="openai",
                            final_text=_openai_text(output),
                            status="incomplete" if response_status != "failed" else "api_error",
                            stop_reason=response_status,
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            events=events,
                            usage=usage,
                            config=config,
                            started=started,
                            tool_calls=tool_calls,
                        )

                    raw_calls: list[dict[str, object]] = []
                    for item_value in output:
                        if not isinstance(item_value, dict):
                            events.append({"type": "invalid_response", "reason": "output_item_not_object"})
                            return result(
                                requested_model=self.model_id,
                                response_model=response_model,
                                provider="openai",
                                final_text="",
                                status="invalid_response",
                                stop_reason="output_item_not_object",
                                system_prompt=system_prompt,
                                user_prompt=user_prompt,
                                events=events,
                                usage=usage,
                                config=config,
                                started=started,
                                tool_calls=tool_calls,
                            )
                        item = cast(dict[str, object], item_value)
                        input_items.append(cast(dict[str, Any], item))
                        if item.get("type") == "function_call":
                            raw_calls.append(item)
                    if raw_calls:
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
                                provider="openai",
                                final_text=_openai_text(output),
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

                        for raw_call in raw_calls:
                            call_id = raw_call.get("call_id")
                            name = raw_call.get("name")
                            arguments, invalid_arguments = parse_tool_arguments(raw_call.get("arguments"))
                            call_started = monotonic()
                            is_error = invalid_arguments or not isinstance(call_id, str) or name not in tool_names
                            if is_error:
                                tool_output: object = {"error": {"type": "InvalidToolCall"}}
                            else:
                                try:
                                    tool_output = await execute_tool(cast(str, name), arguments)
                                except asyncio.CancelledError:
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
                            call_output: dict[str, Any] = {
                                "type": "function_call_output",
                                "call_id": call_id if isinstance(call_id, str) else "invalid-call-id",
                                "output": json_tool_output(tool_output),
                            }
                            caller = raw_call.get("caller")
                            if caller is not None:
                                call_output["caller"] = caller
                            input_items.append(call_output)
                        continue

                    refusal = _openai_refusal(output)
                    final_text = _openai_text(output)
                    if refusal is not None:
                        return result(
                            requested_model=self.model_id,
                            response_model=response_model,
                            provider="openai",
                            final_text=final_text or refusal,
                            status="refused",
                            stop_reason="refusal",
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            events=events,
                            usage=usage,
                            config=config,
                            started=started,
                            tool_calls=tool_calls,
                        )
                    if not final_text:
                        events.append({"type": "invalid_response", "reason": "completed_without_text_or_tool_call"})
                        return result(
                            requested_model=self.model_id,
                            response_model=response_model,
                            provider="openai",
                            final_text="",
                            status="invalid_response",
                            stop_reason="completed_without_text_or_tool_call",
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
                        provider="openai",
                        final_text=final_text,
                        status="completed",
                        stop_reason="completed",
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
                provider="openai",
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
                provider="openai",
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


def _openai_text(output: list[object]) -> str:
    text_parts: list[str] = []
    for item_value in output:
        if not isinstance(item_value, dict):
            continue
        item = cast(dict[str, object], item_value)
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part_value in cast(list[object], content):
            if not isinstance(part_value, dict):
                continue
            part = cast(dict[str, object], part_value)
            text = part.get("text")
            if part.get("type") == "output_text" and isinstance(text, str):
                text_parts.append(text)
    return "\n".join(text_parts).strip()


def _openai_refusal(output: list[object]) -> str | None:
    refusals: list[str] = []
    for item_value in output:
        if not isinstance(item_value, dict):
            continue
        item = cast(dict[str, object], item_value)
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part_value in cast(list[object], content):
            if not isinstance(part_value, dict):
                continue
            part = cast(dict[str, object], part_value)
            refusal = part.get("refusal")
            if part.get("type") == "refusal" and isinstance(refusal, str):
                refusals.append(refusal)
    return "\n".join(refusals).strip() or None
