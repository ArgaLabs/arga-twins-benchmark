from __future__ import annotations

import os
from typing import Literal, cast

from arga_twins_benchmark.agents.anthropic import AnthropicMessagesAdapter
from arga_twins_benchmark.agents.google import GoogleGenerateContentAdapter
from arga_twins_benchmark.agents.models import (
    ModelInvocationResult,
    ToolExecutor,
    ToolSchemaInput,
)
from arga_twins_benchmark.agents.openai import OpenAIResponsesAdapter

SUPPORTED_MODEL_IDS = (
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-fable-5",
    "claude-sonnet-5",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gemini-3.1-pro-preview",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
)


async def invoke_model(
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    tool_schema: ToolSchemaInput,
    execute_tool: ToolExecutor,
    max_tool_calls: int,
    timeout_seconds: float,
    *,
    api_effort: str = "high",
    thinking: str = "adaptive",
) -> ModelInvocationResult:
    """Invoke exactly one preregistered candidate model with no fallback."""

    if model_id in {"claude-opus-4-8", "claude-opus-5", "claude-fable-5", "claude-sonnet-5"}:
        if api_effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError(f"unsupported Anthropic effort {api_effort!r}")
        if thinking not in {"adaptive", "model_default"}:
            raise ValueError(f"unsupported Anthropic thinking mode {thinking!r}")
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        adapter = AnthropicMessagesAdapter(
            api_key=api_key,
            model_id=cast(
                Literal[
                    "claude-opus-4-8",
                    "claude-opus-5",
                    "claude-fable-5",
                    "claude-sonnet-5",
                ],
                model_id,
            ),
            effort=cast(Literal["low", "medium", "high", "xhigh", "max"], api_effort),
            thinking_mode=cast(Literal["adaptive", "model_default"], thinking),
            endpoint=os.environ.get("ANTHROPIC_MESSAGES_URL", "https://api.anthropic.com/v1/messages"),
        )
    elif model_id in {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}:
        if api_effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError(f"unsupported OpenAI effort {api_effort!r}")
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required")
        adapter = OpenAIResponsesAdapter(
            api_key=api_key,
            model_id=cast(
                Literal["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
                model_id,
            ),
            effort=cast(Literal["low", "medium", "high", "xhigh", "max"], api_effort),
            endpoint=os.environ.get("OPENAI_RESPONSES_URL", "https://api.openai.com/v1/responses"),
        )
    elif model_id in {"gemini-3.1-pro-preview", "gemini-3.5-flash", "gemini-3.7-flash"}:
        if api_effort != "default" or thinking != "model_default":
            raise ValueError("Gemini benchmark profiles must use provider-default thinking")
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required")
        adapter = GoogleGenerateContentAdapter(
            api_key=api_key,
            model_id=cast(
                Literal[
                    "gemini-3.1-pro-preview",
                    "gemini-3.5-flash",
                    "gemini-3.7-flash",
                ],
                model_id,
            ),
            endpoint=os.environ.get(
                "GOOGLE_GENERATIVE_LANGUAGE_URL",
                "https://generativelanguage.googleapis.com/v1beta/models",
            ),
        )
    else:
        supported = ", ".join(SUPPORTED_MODEL_IDS)
        raise ValueError(f"unsupported model {model_id!r}; expected one of: {supported}")

    return await adapter.invoke(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        tool_schema=tool_schema,
        execute_tool=execute_tool,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
    )
