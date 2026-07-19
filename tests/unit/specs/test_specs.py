import pytest
from pydantic import ValidationError

from arga_twins_benchmark.specs.models import EpisodeResult, VerificationSpec


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
