from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from arga_twins_benchmark.evaluation.deterministic import CanonicalResource, ToolCallRecord
from arga_twins_benchmark.evaluation.protocol import GradeResult, Mutation
from arga_twins_benchmark.evaluation.state_capture import (
    CapturedProviderState,
    CapturedQueryState,
    TrustedStateSnapshot,
)
from arga_twins_benchmark.evaluation.state_evidence import (
    DeterministicStateEvidence,
    StateEvidenceError,
)
from arga_twins_benchmark.reporting import semantic_grader
from arga_twins_benchmark.runner.matrix import InstanceBundle
from arga_twins_benchmark.specs.models import ComplexitySpec, VerificationSpec


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _snapshot() -> TrustedStateSnapshot:
    return TrustedStateSnapshot(
        providers={
            "test_tracker": CapturedProviderState(
                "test_tracker",
                "tracker",
                {"issues": []},
            )
        },
        queries={
            "issues": CapturedQueryState(
                "issues",
                "test_tracker",
                "tracker",
                "GET",
                "/issues",
                "test_issues",
                200,
                {"issues": []},
            )
        },
    )


def _bundle() -> InstanceBundle:
    return cast(
        InstanceBundle,
        SimpleNamespace(
            binding=SimpleNamespace(roles={"tracker": "test_tracker"}),
            verification=object(),
            instance=SimpleNamespace(complexity=object()),
        ),
    )


def _suite(tmp_path: Path) -> Path:
    suite_dir = tmp_path / "suite"
    trial_id = "suite-1--r1--instance-1--model-1"
    trial_dir = suite_dir / "trials" / trial_id
    model = {"model_id": "model-1", "fallback": False}
    _write_json(
        suite_dir / "suite.json",
        {
            "protocol": "arga-bench-suite/1",
            "suite_run_id": "suite-1",
            "experiment_id": "experiment-1",
            "trials": [
                {
                    "trial_id": trial_id,
                    "instance_id": "instance-1",
                    "model": model,
                }
            ],
        },
    )
    final_text = '{"decision":"completed"}'
    _write_json(
        trial_dir / "result.json",
        {
            "terminal": True,
            "status": "completed",
            "trial_id": trial_id,
            "instance_id": "instance-1",
            "model": model,
            "response_model": "model-1",
            "episode_hash": "episode-hash-1",
            "cleanup_succeeded": True,
            "tool_calls": 6,
            "final_text": final_text,
        },
    )
    _write_json(
        trial_dir / "invocation.json",
        {
            "response_model": "model-1",
            "tool_calls": 6,
            "final_text": final_text,
        },
    )
    _write_json(trial_dir / "baseline-state.json", _snapshot().artifact_payload())
    _write_json(trial_dir / "final-state.json", _snapshot().artifact_payload())
    _write_json(
        trial_dir / "provider-trace.json",
        {
            "protocol": "arga-bench-provider-trace/1",
            "events": [
                {
                    "sequence": sequence,
                    "requested_provider": "tracker",
                    "provider": "test_tracker",
                    "method": "GET",
                    "path": f"/issues/{sequence}",
                    "operation": None,
                    "operation_type": None,
                    "status_code": 200,
                }
                for sequence in range(1, 7)
            ],
        },
    )
    return suite_dir


def _install_catalog_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle()

    def load_bundles(catalog_root: Path, experiment_id: str):
        del catalog_root, experiment_id
        return object(), {"instance-1": bundle}

    def fingerprint(catalog_root: Path, instance_id: str) -> str:
        del catalog_root, instance_id
        return "episode-hash-1"

    monkeypatch.setattr(semantic_grader, "load_experiment_bundles", load_bundles)
    monkeypatch.setattr(semantic_grader, "fingerprint_instance_bundle", fingerprint)
    monkeypatch.setattr(
        semantic_grader,
        "_grader_revision",  # pyright: ignore[reportPrivateUsage]
        lambda: {"commit": "grader-commit-1", "clean": True},
    )


def test_grade_saved_suite_builds_complete_derived_grade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_catalog_stubs(monkeypatch)
    evidence = DeterministicStateEvidence(
        baseline_resources=(),
        resources=(),
        mutations=(),
        raw_delta_count=0,
        canonical_delta_count=0,
        projection_delta_count=0,
    )

    def build_evidence(
        *,
        baseline: TrustedStateSnapshot,
        final: TrustedStateSnapshot,
        verification: VerificationSpec,
    ) -> DeterministicStateEvidence:
        del baseline, final, verification
        return evidence

    def evaluate(
        verification: VerificationSpec,
        *,
        complexity: ComplexitySpec,
        resources: list[CanonicalResource],
        mutations: list[Mutation],
        trace: list[ToolCallRecord],
        output: object,
    ) -> GradeResult:
        del verification, complexity, resources, mutations, trace, output
        return GradeResult(
            task_success=True,
            partial_goal_score=1.0,
            critical_requirements_passed=True,
            collateral_damage=False,
        )

    monkeypatch.setattr(semantic_grader, "build_deterministic_state_evidence", build_evidence)
    monkeypatch.setattr(semantic_grader, "evaluate_deterministic", evaluate)

    report = semantic_grader.grade_saved_suite(_suite(tmp_path), catalog_root=tmp_path)

    assert report["state_grade_complete"] is True
    assert report["scoring_ready"] is True
    assert report["valid_trials"] == 1
    assert report["passed_trials"] == 1
    assert report["by_model"]["model-1"] == {
        "scheduled": 1,
        "valid": 1,
        "invalid_infrastructure": 0,
        "invalid_grader": 0,
        "passed": 1,
        "failed": 0,
        "unsafe": 0,
        "task_success_rate": 1.0,
    }
    assert report["trials"][0]["state_grade_complete"] is True
    assert set(report["trials"][0]["input_sha256"]) == {
        "result.json",
        "invocation.json",
        "baseline-state.json",
        "final-state.json",
        "provider-trace.json",
    }


def test_grade_saved_suite_keeps_unsupported_state_evidence_out_of_scores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_catalog_stubs(monkeypatch)

    def fail_evidence(**kwargs: Any) -> DeterministicStateEvidence:
        del kwargs
        raise StateEvidenceError("canonical projection is incomplete")

    monkeypatch.setattr(semantic_grader, "build_deterministic_state_evidence", fail_evidence)

    report = semantic_grader.grade_saved_suite(_suite(tmp_path), catalog_root=tmp_path)

    assert report["state_grade_complete"] is False
    assert report["valid_trials"] == 0
    assert report["invalid_grader_trials"] == 1
    assert report["passed_trials"] == 0
    assert report["failed_trials"] == 0
    assert report["trials"][0]["outcome"] is None
    assert report["trials"][0]["error"] == {
        "type": "StateEvidenceError",
        "message": "canonical projection is incomplete",
    }
