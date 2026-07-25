from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, cast

type InvocationStatus = Literal[
    "completed",
    "refused",
    "timed_out",
    "tool_limit_exceeded",
    "incomplete",
    "api_error",
    "invalid_response",
]
type ToolSchemaInput = Mapping[str, Any] | Sequence[Mapping[str, Any]]
type ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[object]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ModelInvocationResult:
    requested_model: str
    response_model: str | None
    provider: Literal["anthropic", "openai"]
    final_text: str
    status: InvocationStatus
    stop_reason: str
    system_prompt: str
    user_prompt: str
    events: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    usage: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())
    config: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())
    latency_ms: int = 0
    tool_calls: int = 0

    @property
    def trace(self) -> tuple[dict[str, Any], ...]:
        return self.events

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_tool_schemas(tool_schema: ToolSchemaInput) -> tuple[ToolDefinition, ...]:
    if isinstance(tool_schema, Mapping):
        raw_schemas: Sequence[Mapping[str, Any]] = [tool_schema]
    else:
        raw_schemas = tool_schema

    definitions: list[ToolDefinition] = []
    names: set[str] = set()
    for index, raw in enumerate(raw_schemas):
        name = raw.get("name")
        description = raw.get("description", "")
        input_schema = raw.get("input_schema", raw.get("parameters"))
        if not isinstance(name, str) or not name:
            raise ValueError(f"tool_schema item {index} is missing a non-empty name")
        if name in names:
            raise ValueError(f"duplicate tool name {name!r}")
        if not isinstance(description, str):
            raise ValueError(f"tool {name!r} description must be a string")
        if not isinstance(input_schema, Mapping):
            raise ValueError(f"tool {name!r} must define input_schema or parameters")
        typed_schema = cast(Mapping[str, Any], input_schema)
        definitions.append(
            ToolDefinition(
                name=name,
                description=description,
                input_schema=dict(typed_schema),
            )
        )
        names.add(name)
    if not definitions:
        raise ValueError("at least one tool schema is required")
    return tuple(definitions)
