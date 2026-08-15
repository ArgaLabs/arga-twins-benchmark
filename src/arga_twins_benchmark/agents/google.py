from __future__ import annotations

import asyncio
import ssl
from time import monotonic
from typing import Any, Literal, cast

import httpx

from arga_twins_benchmark.agents.common import api_error_excerpt, elapsed_ms, result
from arga_twins_benchmark.agents.models import (
    ModelInvocationResult,
    ToolExecutor,
    ToolSchemaInput,
    normalize_tool_schemas,
)
from arga_twins_benchmark.errors import RetryableInfrastructureError

GOOGLE_GENERATIVE_LANGUAGE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_OUTPUT_TOKENS = 65_536


def _tool_response(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(cast(dict[str, Any], value))
    if isinstance(value, list):
        return {"result": value}
    if isinstance(value, str | int | float | bool) or value is None:
        return {"result": value}
    return {"error": {"type": "NonJsonToolResult"}}


def _merge_google_usage(total: dict[str, Any], raw_usage: object) -> None:
    if not isinstance(raw_usage, dict):
        return
    usage = cast(dict[str, Any], raw_usage)
    prompt = int(usage.get("promptTokenCount", 0) or 0)
    candidates = int(usage.get("candidatesTokenCount", 0) or 0)
    thoughts = int(usage.get("thoughtsTokenCount", 0) or 0)
    cached = int(usage.get("cachedContentTokenCount", 0) or 0)
    tool_prompt = int(usage.get("toolUsePromptTokenCount", 0) or 0)
    total["input_tokens"] = int(total.get("input_tokens", 0) or 0) + prompt
    total["output_tokens"] = int(total.get("output_tokens", 0) or 0) + candidates + thoughts
    total["cache_read_input_tokens"] = int(total.get("cache_read_input_tokens", 0) or 0) + cached
    total["total_tokens"] = int(total.get("total_tokens", 0) or 0) + int(
        usage.get("totalTokenCount", prompt + candidates + thoughts) or 0
    )
    native = total.setdefault("google_usage", {})
    if not isinstance(native, dict):
        native = {}
        total["google_usage"] = native
    native_usage = cast(dict[str, Any], native)
    for key, value in {
        "prompt_token_count": prompt,
        "candidates_token_count": candidates,
        "thoughts_token_count": thoughts,
        "cached_content_token_count": cached,
        "tool_use_prompt_token_count": tool_prompt,
    }.items():
        native_usage[key] = int(native_usage.get(key, 0) or 0) + value


def _candidate_text(parts: list[object]) -> str:
    texts: list[str] = []
    for raw_part in parts:
        if not isinstance(raw_part, dict):
            continue
        part = cast(dict[str, Any], raw_part)
        if part.get("thought") is True:
            continue
        text = part.get("text")
        if isinstance(text, str):
            texts.append(text)
    return "\n".join(texts).strip()


class GoogleGenerateContentAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        model_id: Literal["gemini-3.1-pro-preview", "gemini-3.5-flash"],
        client: httpx.AsyncClient | None = None,
        endpoint: str = GOOGLE_GENERATIVE_LANGUAGE_URL,
    ) -> None:
        if not api_key:
            raise ValueError("Gemini API key cannot be empty")
        self.api_key = api_key
        self.model_id = model_id
        self.endpoint = endpoint.rstrip("/")
        self._client = client

    def _config(self, *, max_tool_calls: int, timeout_seconds: float) -> dict[str, Any]:
        return {
            "model": self.model_id,
            "provider": "google",
            "endpoint": "generateContent",
            "thinking": "model_default",
            "effort": "default",
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
                "parametersJsonSchema": tool.input_schema,
            }
            for tool in tools
        ]
        config = self._config(max_tool_calls=max_tool_calls, timeout_seconds=timeout_seconds)
        events: list[dict[str, Any]] = [
            {
                "type": "invocation_started",
                "provider": "google",
                "requested_model": self.model_id,
                "config": config,
            }
        ]
        usage: dict[str, Any] = {}
        contents: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": user_prompt}]}]
        tool_calls = 0
        started = monotonic()
        client = self._client or httpx.AsyncClient(timeout=None)
        owns_client = self._client is None
        endpoint = f"{self.endpoint}/{self.model_id}:generateContent"

        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    request_body: dict[str, Any] = {
                        "systemInstruction": {"parts": [{"text": system_prompt}]},
                        "contents": contents,
                        "tools": [{"functionDeclarations": tool_payload}],
                        "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS},
                    }
                    response: httpx.Response | None = None
                    for transport_attempt in range(1, 4):
                        try:
                            response = await client.post(
                                endpoint,
                                headers={
                                    "content-type": "application/json",
                                    "x-goog-api-key": self.api_key,
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
                    if response is None:  # pragma: no cover
                        raise RuntimeError("Google transport loop returned no response")
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
                            response_model=self.model_id,
                            provider="google",
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
                            response_model=self.model_id,
                            provider="google",
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
                    response_usage = response_value.get("usageMetadata")
                    _merge_google_usage(usage, response_usage)
                    candidates = response_value.get("candidates")
                    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
                        block_reason = "missing_candidates"
                        prompt_feedback = response_value.get("promptFeedback")
                        if isinstance(prompt_feedback, dict):
                            raw_reason = cast(dict[str, Any], prompt_feedback).get("blockReason")
                            if isinstance(raw_reason, str):
                                block_reason = raw_reason
                        events.append({"type": "invalid_response", "reason": block_reason})
                        return result(
                            requested_model=self.model_id,
                            response_model=self.model_id,
                            provider="google",
                            final_text="",
                            status="refused" if block_reason != "missing_candidates" else "invalid_response",
                            stop_reason=block_reason,
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            events=events,
                            usage=usage,
                            config=config,
                            started=started,
                            tool_calls=tool_calls,
                        )
                    candidate = cast(dict[str, Any], candidates[0])
                    finish_reason = candidate.get("finishReason")
                    content = candidate.get("content")
                    if not isinstance(content, dict):
                        events.append({"type": "invalid_response", "reason": "missing_candidate_content"})
                        return result(
                            requested_model=self.model_id,
                            response_model=self.model_id,
                            provider="google",
                            final_text="",
                            status="invalid_response",
                            stop_reason="missing_candidate_content",
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            events=events,
                            usage=usage,
                            config=config,
                            started=started,
                            tool_calls=tool_calls,
                        )
                    raw_parts = cast(dict[str, Any], content).get("parts")
                    if not isinstance(raw_parts, list):
                        events.append({"type": "invalid_response", "reason": "missing_candidate_parts"})
                        return result(
                            requested_model=self.model_id,
                            response_model=self.model_id,
                            provider="google",
                            final_text="",
                            status="invalid_response",
                            stop_reason="missing_candidate_parts",
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            events=events,
                            usage=usage,
                            config=config,
                            started=started,
                            tool_calls=tool_calls,
                        )
                    parts = cast(list[object], raw_parts)
                    events.append(
                        {
                            "type": "assistant_response",
                            "response_model": self.model_id,
                            "finish_reason": finish_reason,
                            "content": content,
                            "usage": response_usage,
                        }
                    )
                    # Preserve Google's model content byte-for-byte at the JSON value level. Gemini 3
                    # requires thoughtSignature fields to be returned on subsequent tool turns.
                    contents.append(cast(dict[str, Any], content))

                    raw_calls: list[dict[str, Any]] = []
                    for raw_part in parts:
                        if not isinstance(raw_part, dict):
                            continue
                        function_call = cast(dict[str, Any], raw_part).get("functionCall")
                        if isinstance(function_call, dict):
                            raw_calls.append(cast(dict[str, Any], function_call))
                    final_text = _candidate_text(parts)
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
                                response_model=self.model_id,
                                provider="google",
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

                        response_parts: list[dict[str, Any]] = []
                        for raw_call in raw_calls:
                            call_id = raw_call.get("id")
                            name = raw_call.get("name")
                            raw_args = raw_call.get("args")
                            arguments = dict(cast(dict[str, Any], raw_args)) if isinstance(raw_args, dict) else {}
                            call_started = monotonic()
                            is_error = (
                                not isinstance(call_id, str)
                                or name not in tool_names
                                or not isinstance(raw_args, dict)
                            )
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
                            function_response: dict[str, Any] = {
                                "id": call_id if isinstance(call_id, str) else "invalid-call-id",
                                "name": name if isinstance(name, str) else "invalid-tool-name",
                                "response": _tool_response(tool_output),
                            }
                            response_parts.append({"functionResponse": function_response})
                        # Gemini requires all parallel function responses in one user content.
                        contents.append({"role": "user", "parts": response_parts})
                        continue

                    if finish_reason == "STOP" and final_text:
                        return result(
                            requested_model=self.model_id,
                            response_model=self.model_id,
                            provider="google",
                            final_text=final_text,
                            status="completed",
                            stop_reason="STOP",
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            events=events,
                            usage=usage,
                            config=config,
                            started=started,
                            tool_calls=tool_calls,
                        )
                    status = "refused" if finish_reason in {"SAFETY", "PROHIBITED_CONTENT"} else "incomplete"
                    return result(
                        requested_model=self.model_id,
                        response_model=self.model_id,
                        provider="google",
                        final_text=final_text,
                        status=status,
                        stop_reason=str(finish_reason or "empty_response"),
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
                response_model=self.model_id,
                provider="google",
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
                response_model=self.model_id,
                provider="google",
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
