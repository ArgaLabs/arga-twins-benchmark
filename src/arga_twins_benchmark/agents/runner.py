from __future__ import annotations

import os

from arga_twins_benchmark.agents.anthropic import AnthropicMessagesAdapter
from arga_twins_benchmark.agents.models import (
    ModelInvocationResult,
    ToolExecutor,
    ToolSchemaInput,
)
from arga_twins_benchmark.agents.openai import OpenAIResponsesAdapter

SUPPORTED_MODEL_IDS = (
    "claude-opus-4-8",
    "claude-fable-5",
    "gpt-5.6-sol",
)


async def invoke_model(
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    tool_schema: ToolSchemaInput,
    execute_tool: ToolExecutor,
    max_tool_calls: int,
    timeout_seconds: float,
) -> ModelInvocationResult:
    """Invoke exactly one preregistered candidate model with no fallback."""

    if model_id == "claude-opus-4-8" or model_id == "claude-fable-5":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        adapter = AnthropicMessagesAdapter(
            api_key=api_key,
            model_id=model_id,
            endpoint=os.environ.get("ANTHROPIC_MESSAGES_URL", "https://api.anthropic.com/v1/messages"),
        )
    elif model_id == "gpt-5.6-sol":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required")
        adapter = OpenAIResponsesAdapter(
            api_key=api_key,
            endpoint=os.environ.get("OPENAI_RESPONSES_URL", "https://api.openai.com/v1/responses"),
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

