from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from arga_twins_benchmark.specs.models import StrictModel

CONFORMANCE_REGISTRY_PROTOCOL = "arga-bench-verifier-conformance-registry/1"
EVALUATOR_FIXTURE_PROTOCOL = "arga-bench-evaluator-conformance-fixture/1"
CONFORMANCE_AUDIT_PROTOCOL = "arga-bench-verifier-conformance-audit/1"
LIVE_LIFECYCLE_PROTOCOL = "arga-bench-live-lifecycle-conformance/1"
LIVE_CASE_EVIDENCE_PROTOCOL = "arga-bench-live-case-conformance/1"


class ConformanceCaseKind(StrEnum):
    GOLD = "gold"
    NEGATIVE_CONTROL = "negative_control"
    SEMANTIC_EQUIVALENT = "semantic_equivalent"


class StaticCaseStatus(StrEnum):
    PENDING = "pending"
    EXECUTABLE = "executable"


class ConformanceCaseSpec(StrictModel):
    case_id: str = Field(min_length=1)
    kind: ConformanceCaseKind
    declared_control_id: str | None = None
    reference_gold_id: str = Field(min_length=1)
    expected_task_success: bool
    expected_collateral_damage: bool | None = None
    intended_failure_assertion_ids: list[str] = Field(default_factory=list)
    static_status: StaticCaseStatus
    fixture_id: str | None = None
    pending_reason: str | None = None

    @model_validator(mode="after")
    def validate_case_contract(self) -> ConformanceCaseSpec:
        if self.kind == ConformanceCaseKind.GOLD:
            if self.declared_control_id != self.case_id or self.reference_gold_id != self.case_id:
                raise ValueError("gold cases must use their declared gold ID as case_id and reference_gold_id")
            if not self.expected_task_success:
                raise ValueError("gold cases must expect task success")
        elif self.kind == ConformanceCaseKind.NEGATIVE_CONTROL:
            if self.declared_control_id != self.case_id:
                raise ValueError("negative controls must use their declared control ID as case_id")
            if self.expected_task_success:
                raise ValueError("negative controls must expect task failure")
        else:
            if self.declared_control_id is not None:
                raise ValueError("semantic-equivalent cases are not declared verifier controls")
            if not self.expected_task_success:
                raise ValueError("semantic-equivalent cases must expect task success")

        if self.static_status == StaticCaseStatus.EXECUTABLE:
            if self.fixture_id != self.case_id:
                raise ValueError("executable cases must use their case ID as fixture_id")
            if self.pending_reason is not None:
                raise ValueError("executable cases cannot declare a pending_reason")
        else:
            if self.fixture_id is not None:
                raise ValueError("pending cases cannot reference an evaluator fixture")
            if not self.pending_reason:
                raise ValueError("pending cases must explain what evidence is missing")
        return self


class ResetIsolationRequirement(StrictModel):
    required_equivalent_resets: int = Field(default=10, ge=10)
    required_gold_repeats: int = Field(default=3, ge=3)
    require_independent_run: bool = True
    require_mutation_visibility_probe: bool = True
    status: Literal["pending_live"] = "pending_live"
    pending_reason: str = Field(min_length=1)


class VerifierConformanceEntry(StrictModel):
    instance_id: str = Field(min_length=1)
    verifier_id: str = Field(min_length=1)
    instance_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gold: ConformanceCaseSpec
    semantic_equivalent: ConformanceCaseSpec
    negative_controls: list[ConformanceCaseSpec] = Field(min_length=1)
    lifecycle: ResetIsolationRequirement

    @model_validator(mode="after")
    def validate_case_relationships(self) -> VerifierConformanceEntry:
        if self.gold.kind != ConformanceCaseKind.GOLD:
            raise ValueError("entry.gold must be a gold case")
        if self.semantic_equivalent.kind != ConformanceCaseKind.SEMANTIC_EQUIVALENT:
            raise ValueError("entry.semantic_equivalent must be a semantic-equivalent case")
        if self.semantic_equivalent.reference_gold_id != self.gold.case_id:
            raise ValueError("semantic-equivalent case must reference the entry gold case")
        if any(case.kind != ConformanceCaseKind.NEGATIVE_CONTROL for case in self.negative_controls):
            raise ValueError("entry.negative_controls may contain only negative controls")
        if any(case.reference_gold_id != self.gold.case_id for case in self.negative_controls):
            raise ValueError("every negative control must reference the entry gold case")
        case_ids = [
            self.gold.case_id,
            self.semantic_equivalent.case_id,
            *(case.case_id for case in self.negative_controls),
        ]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("conformance case IDs must be unique within an entry")
        return self


class ConformanceRegistry(StrictModel):
    kind: Literal["verifier_conformance_registry"]
    protocol: Literal["arga-bench-verifier-conformance-registry/1"]
    schema_version: Literal["0.1"] = "0.1"
    entries: list[VerifierConformanceEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_entries(self) -> ConformanceRegistry:
        instance_ids = [entry.instance_id for entry in self.entries]
        verifier_ids = [entry.verifier_id for entry in self.entries]
        case_ids = [
            case.case_id
            for entry in self.entries
            for case in (entry.gold, entry.semantic_equivalent, *entry.negative_controls)
        ]
        if len(instance_ids) != len(set(instance_ids)):
            raise ValueError("conformance registry instance IDs must be unique")
        if len(verifier_ids) != len(set(verifier_ids)):
            raise ValueError("conformance registry verifier IDs must be unique")
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("conformance registry case IDs must be globally unique")
        return self


class JsonPatchOperation(StrictModel):
    op: Literal["add", "replace", "remove"]
    path: str = Field(pattern=r"^/(?:[^/]*)?(?:/[^/]*)*$")
    value: Any = None

    @model_validator(mode="after")
    def validate_value(self) -> JsonPatchOperation:
        if any(re.search(r"~(?:[^01]|$)", part) is not None for part in self.path.split("/")[1:]):
            raise ValueError("JSON patch path contains an invalid JSON pointer escape")
        value_declared = "value" in self.model_fields_set
        if self.op == "remove" and value_declared:
            raise ValueError("remove patches cannot declare value")
        if self.op in {"add", "replace"} and not value_declared:
            raise ValueError(f"{self.op} patches must declare value, including explicit null")
        return self


class ToolCallFixture(StrictModel):
    provider_role: str = Field(min_length=1)
    method: str = Field(min_length=1)
    path: str = Field(min_length=1)
    status_code: int | None = None
    mutating: bool
    operation: str | None = None
    source: Literal["candidate", "verifier", "seed"] = "candidate"
    destination: Literal["provisioned_provider", "external", "control_plane", "agent_adapter"] = (
        "provisioned_provider"
    )
    sequence: int | None = Field(default=None, ge=0)
    request_fingerprint: str | None = None
    action_fingerprint: str | None = None
    attempt_fingerprint: str | None = None


class CanonicalResourceFixture(StrictModel):
    provider_role: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    fields: dict[str, Any] = Field(default_factory=dict)


class MutationFixture(StrictModel):
    twin: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    field: str | None = None
    before: Any = None
    after: Any = None


class GradeExpectation(StrictModel):
    task_success: bool
    collateral_damage: bool
    trace_policy_passed: bool | None = None
    required_passed_assertion_ids: list[str] = Field(default_factory=list)
    required_failed_assertion_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_assertion_sets(self) -> GradeExpectation:
        overlap = set(self.required_passed_assertion_ids) & set(self.required_failed_assertion_ids)
        if overlap:
            raise ValueError(f"fixture assertions cannot be both passing and failing: {sorted(overlap)}")
        return self


class StateEvidenceFixtureCase(StrictModel):
    evidence_kind: Literal["trusted_state_patch"]
    case_id: str = Field(min_length=1)
    final_patch: list[JsonPatchOperation]
    trace: list[ToolCallFixture]
    output: Any
    expected: GradeExpectation


class CanonicalEvidenceFixtureCase(StrictModel):
    evidence_kind: Literal["canonical_projection"]
    case_id: str = Field(min_length=1)
    resources: list[CanonicalResourceFixture]
    mutations: list[MutationFixture]
    trace: list[ToolCallFixture]
    output: Any
    expected: GradeExpectation


type EvaluatorFixtureCase = StateEvidenceFixtureCase | CanonicalEvidenceFixtureCase


class EvaluatorFixtureBundle(StrictModel):
    kind: Literal["evaluator_conformance_fixture"]
    protocol: Literal["arga-bench-evaluator-conformance-fixture/1"]
    schema_version: Literal["0.1"] = "0.1"
    instance_id: str = Field(min_length=1)
    source: Literal["synthetic_state_evidence", "preserved_trusted_evidence"]
    source_note: str = Field(min_length=1)
    baseline_state: dict[str, Any] | None = None
    cases: list[EvaluatorFixtureCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bundle(self) -> EvaluatorFixtureBundle:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("fixture bundle case IDs must be unique")
        if any(isinstance(case, StateEvidenceFixtureCase) for case in self.cases):
            if self.baseline_state is None:
                raise ValueError("trusted-state patch cases require a baseline_state")
        return self


class LiveCaseObservation(StrictModel):
    case_id: str = Field(min_length=1)
    repeat: int = Field(ge=1)
    task_success: bool
    collateral_damage: bool
    assertion_results: dict[str, bool]
    baseline_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    final_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LiveCaseEvidence(StrictModel):
    protocol: Literal["arga-bench-live-case-conformance/1"]
    schema_version: Literal["0.1"] = "0.1"
    instance_id: str = Field(min_length=1)
    instance_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observations: list[LiveCaseObservation] = Field(min_length=1)


class LiveLifecycleEvidence(StrictModel):
    protocol: Literal["arga-bench-live-lifecycle-conformance/1"]
    schema_version: Literal["0.1"] = "0.1"
    instance_id: str = Field(min_length=1)
    instance_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_reset_count: int = Field(ge=10)
    baseline_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reset_state_sha256: list[str] = Field(min_length=10)
    independent_run_state_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    independent_run_proved: bool = False
    mutation_visibility_proved: bool = False
    mutation_probe_state_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    post_probe_reset_state_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    cleanup_confirmed: bool

    @model_validator(mode="after")
    def validate_lifecycle_evidence(self) -> LiveLifecycleEvidence:
        if len(self.reset_state_sha256) != self.required_reset_count:
            raise ValueError("reset-state hashes must exactly match required_reset_count")
        if self.independent_run_proved and self.independent_run_state_sha256 is None:
            raise ValueError("independent-run proof requires its state hash")
        if self.mutation_visibility_proved and (
            self.mutation_probe_state_sha256 is None or self.post_probe_reset_state_sha256 is None
        ):
            raise ValueError("mutation-visibility proof requires mutated and post-reset hashes")
        return self
