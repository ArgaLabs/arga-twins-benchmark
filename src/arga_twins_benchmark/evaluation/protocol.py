from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]

OUTCOME_FIRST_GRADING_POLICY = "outcome_first_v1"
EFFICIENCY_DIAGNOSTICS_PROTOCOL = "arga-bench-efficiency-diagnostics/1"
REDUNDANT_CALL_FLAG_COUNT = 5


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
class RedundantCallGroup:
    code: str
    provider_role: str
    method: str
    path: str
    mutating: bool
    total_count: int
    successful_count: int
    failed_count: int
    repeat_count: int
    call_indices: list[int] = field(default_factory=lambda: list[int]())
    epoch: int | None = None
    fingerprint_scope: str = "action"


@dataclass(frozen=True)
class EfficiencyDiagnostics:
    protocol: str = EFFICIENCY_DIAGNOSTICS_PROTOCOL
    flagged: bool = False
    analysis_completeness: str = "exact"
    equivalent_call_flag_count: int = REDUNDANT_CALL_FLAG_COUNT
    total_candidate_calls: int = 0
    distinct_actions: int = 0
    unfingerprinted_call_count: int = 0
    flagged_repeat_attempts: int = 0
    groups: list[RedundantCallGroup] = field(default_factory=lambda: list[RedundantCallGroup]())


@dataclass(frozen=True)
class GradeDiagnostics:
    trace_policy_passed: bool = True
    trace_policy_failures: list[str] = field(default_factory=lambda: list[str]())
    unmatched_mutating_call_count: int = 0
    efficiency: EfficiencyDiagnostics = field(default_factory=EfficiencyDiagnostics)


@dataclass(frozen=True)
class GradeResult:
    task_success: bool
    partial_goal_score: float
    critical_requirements_passed: bool
    collateral_damage: bool
    harm_vector: list[str] = field(default_factory=lambda: list[str]())
    assertion_results: dict[str, bool] = field(default_factory=lambda: dict[str, bool]())
    grading_policy: str = OUTCOME_FIRST_GRADING_POLICY
    hard_assertion_ids: list[str] = field(default_factory=lambda: list[str]())
    diagnostic_assertion_ids: list[str] = field(default_factory=lambda: list[str]())
    diagnostics: GradeDiagnostics = field(default_factory=GradeDiagnostics)


class Verifier(Protocol):
    def evaluate(
        self,
        *,
        before: dict[str, Any],
        after: dict[str, Any],
        output: Any,
        mutations: list[Mutation],
    ) -> GradeResult: ...
