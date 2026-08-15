from __future__ import annotations

import json
from time import monotonic
from typing import Any, Literal, cast

from arga_twins_benchmark.agents.models import InvocationStatus, ModelInvocationResult


def elapsed_ms(started: float) -> int:
    return round((monotonic() - started) * 1000)


def json_tool_output(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return json.dumps(
            {"error": {"type": "NonJsonToolResult"}},
            separators=(",", ":"),
        )


def parse_tool_arguments(value: object) -> tuple[dict[str, Any], bool]:
    if isinstance(value, dict):
        return dict(cast(dict[str, Any], value)), False
    if isinstance(value, str):
        try:
            parsed: object = json.loads(value)
        except json.JSONDecodeError:
            return {}, True
        if isinstance(parsed, dict):
            return cast(dict[str, Any], parsed), False
    return {}, True


def merge_usage(total: dict[str, Any], usage: object) -> None:
    if not isinstance(usage, dict):
        return
    typed_usage = cast(dict[str, object], usage)
    for raw_key, value in typed_usage.items():
        key = str(raw_key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            existing = total.get(key, 0)
            total[key] = existing + value if isinstance(existing, int | float) else value
        elif isinstance(value, dict):
            nested = total.setdefault(key, {})
            if not isinstance(nested, dict):
                nested = {}
                total[key] = nested
            merge_usage(
                cast(dict[str, Any], nested),
                cast(dict[str, object], value),
            )


def api_error_excerpt(response_text: str, *, limit: int = 2_000) -> str:
    return response_text[:limit]


def result(
    *,
    requested_model: str,
    response_model: str | None,
    provider: Literal["anthropic", "openai", "google"],
    final_text: str,
    status: InvocationStatus,
    stop_reason: str,
    system_prompt: str,
    user_prompt: str,
    events: list[dict[str, Any]],
    usage: dict[str, Any],
    config: dict[str, Any],
    started: float,
    tool_calls: int,
) -> ModelInvocationResult:
    return ModelInvocationResult(
        requested_model=requested_model,
        response_model=response_model,
        provider=provider,
        final_text=final_text,
        status=status,
        stop_reason=stop_reason,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        events=tuple(events),
        usage=usage,
        config=config,
        latency_ms=elapsed_ms(started),
        tool_calls=tool_calls,
    )
