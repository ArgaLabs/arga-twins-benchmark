from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True)
class Mutation:
    twin: str
    resource_type: str
    resource_id: str
    operation: str
    field: str | None = None
    before: JsonValue = None
    after: JsonValue = None


@dataclass(frozen=True)
class GradeResult:
    task_success: bool
    partial_goal_score: float
    critical_requirements_passed: bool
    collateral_damage: bool
    harm_vector: list[str] = field(default_factory=lambda: list[str]())
    assertion_results: dict[str, bool] = field(default_factory=lambda: dict[str, bool]())


class Verifier(Protocol):
    def evaluate(
        self,
        *,
        before: dict[str, Any],
        after: dict[str, Any],
        output: Any,
        mutations: list[Mutation],
    ) -> GradeResult: ...
