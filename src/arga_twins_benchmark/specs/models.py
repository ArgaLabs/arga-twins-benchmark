from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Track(StrEnum):
    CAPABILITY = "capability"
    SAFETY = "safety"
    ROBUSTNESS = "robustness"


class VariantKind(StrEnum):
    CLEAN = "clean"
    DISTRACTOR = "distractor"
    OPERATIONAL_HURDLE = "operational_hurdle"
    PROVIDER_CONTRAST = "provider_contrast"
    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"
    AMBIGUOUS = "ambiguous"
    ADVERSARIAL = "adversarial"
    IDEMPOTENT = "idempotent"


class TrialValidity(StrEnum):
    VALID = "valid"
    INVALID_INFRASTRUCTURE = "invalid_infrastructure"
    INVALID_GRADER = "invalid_grader"


class AgentOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNSAFE = "unsafe"
    REFUSED = "refused"
    TIMED_OUT = "timed_out"
    RUNTIME_ERROR = "runtime_error"


class HarmCategory(StrEnum):
    CONFIDENTIALITY_VIOLATION = "confidentiality_violation"
    UNAUTHORIZED_ACCESS_CHANGE = "unauthorized_access_change"
    DESTRUCTIVE_MUTATION = "destructive_mutation"
    FINANCIAL_INTEGRITY_VIOLATION = "financial_integrity_violation"
    IMPERSONATION_COMPLIANCE = "impersonation_compliance"
    PROMPT_INJECTION_COMPLIANCE = "prompt_injection_compliance"
    CROSS_TENANT_VIOLATION = "cross_tenant_violation"


class IdentitySpec(StrictModel):
    id: str
    display_name: str
    email: str | None = None
    roles: list[str] = Field(default_factory=list)


class WorldSpec(StrictModel):
    kind: Literal["world"]
    schema_version: Literal["0.1"] = "0.1"
    world_id: str
    controlled_clock: datetime
    tenants: list[str] = Field(min_length=1)
    identities: list[IdentitySpec] = Field(default_factory=lambda: list[IdentitySpec]())
    invariants: list[str] = Field(default_factory=list)


class BindingSpec(StrictModel):
    kind: Literal["binding"]
    schema_version: Literal["0.1"] = "0.1"
    binding_id: str
    roles: dict[str, str] = Field(min_length=1)
    provider_contracts: dict[str, str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_provider_contracts(self) -> BindingSpec:
        missing_contracts = set(self.roles.values()) - set(self.provider_contracts)
        if missing_contracts:
            raise ValueError(f"missing provider contracts for {sorted(missing_contracts)}")
        return self


class TemplateSpec(StrictModel):
    kind: Literal["template"]
    schema_version: Literal["0.1"] = "0.1"
    template_id: str
    title: str
    description: str
    track: Track
    required_roles: list[str] = Field(min_length=1)
    variants: list[VariantKind] = Field(min_length=1)


class BudgetSpec(StrictModel):
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    max_tool_calls: int = Field(default=50, ge=1, le=1000)


class AuthorizationSpec(StrictModel):
    principal: str
    tenant: str
    allowed_actions: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)


class InstanceSpec(StrictModel):
    kind: Literal["instance"]
    schema_version: Literal["0.1"] = "0.1"
    instance_id: str
    template_id: str
    variant_group: str
    variant: VariantKind
    split: Literal["development", "public_test", "private_test", "challenge"]
    world_id: str
    binding_id: str
    prompt_file: str
    seed_files: dict[str, str] = Field(min_length=1)
    verification_file: str
    authorization: AuthorizationSpec
    budget: BudgetSpec = Field(default_factory=BudgetSpec)
    setup_patches: list[str] = Field(default_factory=list)
    failure_schedule: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerificationSpec(StrictModel):
    kind: Literal["verification"]
    schema_version: Literal["0.1"] = "0.1"
    verifier_id: str
    gold_solution_id: str
    negative_control_ids: list[str] = Field(min_length=1)
    expected_state: list[str] = Field(min_length=1)
    allowed_state_changes: list[str] = Field(default_factory=list)
    forbidden_state_changes: list[str] = Field(min_length=1)
    critical_requirements: list[str] = Field(min_length=1)
    partial_credit: dict[str, float] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_partial_credit(self) -> VerificationSpec:
        if any(weight < 0 or weight > 1 for weight in self.partial_credit.values()):
            raise ValueError("partial-credit weights must be between 0 and 1")
        if self.partial_credit and not math.isclose(sum(self.partial_credit.values()), 1.0, abs_tol=1e-9):
            raise ValueError("partial-credit weights must sum to 1")
        return self


class ExperimentSpec(StrictModel):
    kind: Literal["experiment"]
    schema_version: Literal["0.1"] = "0.1"
    experiment_id: str
    instances: list[str] = Field(min_length=1)
    repeats: int = Field(default=1, ge=1, le=20)
    random_seed: int = 0
    concurrency: int = Field(default=1, ge=1, le=100)


class EpisodeResult(StrictModel):
    kind: Literal["episode_result"] = "episode_result"
    schema_version: Literal["0.1"] = "0.1"
    trial_id: str
    suite_run_id: str
    instance_id: str
    episode_hash: str
    trial_validity: TrialValidity
    agent_outcome: AgentOutcome
    task_success: bool
    partial_goal_score: float = Field(ge=0, le=1)
    harm_vector: list[HarmCategory] = Field(default_factory=lambda: list[HarmCategory]())
    collateral_damage: bool = False
    over_refusal: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_outcome_consistency(self) -> EpisodeResult:
        if self.trial_validity is not TrialValidity.VALID:
            if self.task_success or self.agent_outcome is AgentOutcome.PASSED:
                raise ValueError("invalid trials cannot pass")
            if self.over_refusal:
                raise ValueError("invalid trials cannot count as over-refusals")
        if self.task_success:
            if self.agent_outcome is not AgentOutcome.PASSED:
                raise ValueError("task success requires a passed agent outcome")
            if self.collateral_damage or self.harm_vector:
                raise ValueError("task success cannot include collateral damage or safety harm")
        if self.agent_outcome is AgentOutcome.UNSAFE and not self.harm_vector:
            raise ValueError("unsafe outcomes require at least one harm category")
        if self.over_refusal and self.agent_outcome is not AgentOutcome.REFUSED:
            raise ValueError("over-refusal requires a refused agent outcome")
        return self


CatalogModel = WorldSpec | BindingSpec | TemplateSpec | InstanceSpec | VerificationSpec | ExperimentSpec
