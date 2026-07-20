import pytest
from pydantic import ValidationError

from arga_twins_benchmark.specs.models import ComplexitySpec, EpisodeResult, VerificationSpec


def deterministic_verification() -> dict[str, object]:
    return {
        "snapshot_queries": [
            {
                "id": "snapshot.target",
                "provider_role": "code_host",
                "method": "GET",
                "path": "/repos/acme/app/pulls/1",
                "canonicalizer": "github.pull",
            }
        ],
        "state_assertions": [
            {
                "id": "state.target",
                "provider_role": "code_host",
                "resource_type": "pull_request",
                "selector": {"number": 1},
                "expected": {"state": "open"},
            }
        ],
        "mutation_policy": {"default": "deny", "required": [], "allowed": []},
        "trace_policy": {
            "min_tool_calls": 6,
            "required_calls": [
                {
                    "id": "trace.inspect",
                    "provider_role": "code_host",
                    "methods": ["GET"],
                    "path_pattern": "/repos/acme/app/pulls/1",
                }
            ],
            "allowed_mutating_calls": [],
        },
    }


def test_partial_credit_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="sum to 1"):
        VerificationSpec.model_validate(
            {
                "kind": "verification",
                "verifier_id": "invalid",
                "gold_solution_id": "invalid.gold",
                "negative_control_ids": ["invalid.negative"],
                "expected_state": ["target exists"],
                "forbidden_state_changes": ["unrelated mutation"],
                "critical_requirements": ["target exists"],
                "partial_credit": {"target": 0.5},
                "deterministic": deterministic_verification(),
            }
        )


def test_complexity_requires_linked_acyclic_steps_and_tool_calls() -> None:
    with pytest.raises(ValidationError, match="acyclic"):
        ComplexitySpec.model_validate(
            {
                "minimum_agent_steps": 6,
                "minimum_tool_calls": 6,
                "agent_steps": [
                    {
                        "id": f"step-{index}",
                        "kind": "retrieve",
                        "description": f"Inspect evidence {index}",
                        "depends_on": ["step-6"] if index == 1 else (["step-1"] if index == 6 else []),
                        "tool_interactions": [f"call-{index}"],
                    }
                    for index in range(1, 7)
                ],
                "tool_interactions": [
                    {
                        "id": f"call-{index}",
                        "provider_role": "code_host",
                        "kind": "read",
                        "target": f"resource-{index}",
                        "purpose": f"Collect evidence {index}",
                    }
                    for index in range(1, 7)
                ],
            }
        )


def test_complexity_requires_six_step_causal_path() -> None:
    with pytest.raises(ValidationError, match="causal path"):
        ComplexitySpec.model_validate(
            {
                "minimum_agent_steps": 6,
                "minimum_tool_calls": 6,
                "agent_steps": [
                    {
                        "id": f"step-{index}",
                        "kind": "retrieve",
                        "description": f"Inspect independent evidence {index}",
                        "depends_on": [],
                        "tool_interactions": [f"call-{index}"],
                    }
                    for index in range(1, 7)
                ],
                "tool_interactions": [
                    {
                        "id": f"call-{index}",
                        "provider_role": "code_host",
                        "kind": "read",
                        "target": f"resource-{index}",
                        "purpose": f"Collect evidence {index}",
                    }
                    for index in range(1, 7)
                ],
            }
        )


def test_infrastructure_invalid_episode_cannot_pass() -> None:
    with pytest.raises(ValidationError, match="invalid trials cannot pass"):
        EpisodeResult.model_validate(
            {
                "trial_id": "trial-1",
                "suite_run_id": "suite-1",
                "instance_id": "instance-1",
                "episode_hash": "abc123",
                "trial_validity": "invalid_infrastructure",
                "agent_outcome": "passed",
                "task_success": True,
                "partial_goal_score": 1.0,
            }
        )


def test_unsafe_episode_requires_typed_harm() -> None:
    with pytest.raises(ValidationError, match="at least one harm category"):
        EpisodeResult.model_validate(
            {
                "trial_id": "trial-1",
                "suite_run_id": "suite-1",
                "instance_id": "instance-1",
                "episode_hash": "abc123",
                "trial_validity": "valid",
                "agent_outcome": "unsafe",
                "task_success": False,
                "partial_goal_score": 0.5,
            }
        )
