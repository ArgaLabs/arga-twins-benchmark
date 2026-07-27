from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from arga_twins_benchmark.evaluation.deterministic import CanonicalResource, ToolCallRecord
from arga_twins_benchmark.evaluation.protocol import (
    EfficiencyDiagnostics,
    GradeDiagnostics,
    GradeResult,
    Mutation,
    RedundantCallGroup,
)
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
            instance_path=Path("instance.yaml"),
            binding=SimpleNamespace(roles={"tracker": "test_tracker"}),
            verification=object(),
            instance=SimpleNamespace(complexity=object(), seed_files={}),
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
    _write_json(
        trial_dir / "control.json",
        {
            "instance_id": "instance-1",
            "scenario_content_sha256": "episode-hash-1",
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

    def recover_snapshots(
        *,
        baseline: TrustedStateSnapshot,
        final: TrustedStateSnapshot,
        invocation: Mapping[str, Any],
        trace_payload: Mapping[str, Any],
        control_payload: Mapping[str, Any],
        instance_id: str,
        instance_path: Path,
        seed_files: Mapping[str, str],
        expected_episode_hash: str,
    ) -> tuple[TrustedStateSnapshot, TrustedStateSnapshot]:
        del (
            invocation,
            trace_payload,
            control_payload,
            instance_id,
            instance_path,
            seed_files,
            expected_episode_hash,
        )
        return baseline, final

    def passing_audit(suite_dir: Path) -> dict[str, object]:
        del suite_dir
        return {
            "integrity_passed": True,
            "matrix_fully_evaluable": True,
        }

    monkeypatch.setattr(semantic_grader, "load_experiment_bundles", load_bundles)
    monkeypatch.setattr(semantic_grader, "fingerprint_instance_bundle", fingerprint)
    monkeypatch.setattr(
        semantic_grader,
        "recover_preserved_trial_snapshots",
        recover_snapshots,
    )
    monkeypatch.setattr(
        semantic_grader,
        "_grader_revision",  # pyright: ignore[reportPrivateUsage]
        lambda: {"commit": "grader-commit-1", "clean": True},
    )
    monkeypatch.setattr(
        semantic_grader,
        "audit_suite",
        passing_audit,
    )


def test_legacy_invocation_arguments_reconstruct_exact_action_fingerprints() -> None:
    trace_payload = {
        "protocol": "arga-bench-provider-trace/1",
        "events": [
            {
                "sequence": sequence,
                "requested_provider": "tracker",
                "provider": "test_tracker",
                "method": "POST",
                "path": "/issues/search",
                "operation": None,
                "operation_type": None,
                "status_code": 200,
            }
            for sequence in range(1, 6)
        ],
    }
    invocation = {
        "events": [
            {
                "type": "tool_call",
                "arguments": {
                    "provider": "tracker",
                    "method": "POST",
                    "path": "/issues/search",
                    "body": {"query": "same request"},
                },
                "output": {"trace": {"sequence": sequence}},
            }
            for sequence in range(1, 6)
        ]
    }

    records = semantic_grader._trace_records(  # pyright: ignore[reportPrivateUsage]
        trace_payload,
        bundle=_bundle(),
        invocation=invocation,
    )

    assert len(records) == 5
    assert records[0].action_fingerprint is not None
    assert len({record.action_fingerprint for record in records}) == 1


def test_rejected_unbound_provider_attempts_remain_valid_trace_evidence() -> None:
    trace_payload = {
        "protocol": "arga-bench-provider-trace/1",
        "events": [
            {
                "sequence": sequence,
                "requested_provider": "wrong-provider",
                "provider": None,
                "method": "POST",
                "path": "/issues/search",
                "operation": None,
                "operation_type": None,
                "status_code": None,
                "error": "unknown provider",
            }
            for sequence in range(1, 6)
        ],
    }
    invocation = {
        "events": [
            {
                "type": "tool_call",
                "arguments": {
                    "provider": "wrong-provider",
                    "method": "POST",
                    "path": "/issues/search",
                    "body": {"query": "same request"},
                },
                "output": {"trace": {"sequence": sequence}},
            }
            for sequence in range(1, 6)
        ]
    }

    records = semantic_grader._trace_records(  # pyright: ignore[reportPrivateUsage]
        trace_payload,
        bundle=_bundle(),
        invocation=invocation,
    )

    assert len(records) == 5
    assert {record.provider_role for record in records} == {"unbound_provider"}
    assert records[0].action_fingerprint is None
    assert records[0].attempt_fingerprint is not None
    assert len({record.attempt_fingerprint for record in records}) == 1


def test_identical_model_adapter_rejections_get_trusted_attempt_fingerprints() -> None:
    invocation = {
        "events": [
            {
                "type": "tool_call",
                "provider_call_index": sequence,
                "name": "unknown_secret_tool",
                "arguments": {"same": "candidate-private-value"},
                "output": {"error": {"type": "InvalidToolCall"}},
                "is_error": True,
            }
            for sequence in range(1, 6)
        ]
    }

    records = semantic_grader._adapter_rejected_records(invocation)  # pyright: ignore[reportPrivateUsage]

    assert len(records) == 5
    assert {record.provider_role for record in records} == {"agent_adapter"}
    assert {record.destination for record in records} == {"agent_adapter"}
    assert [record.sequence for record in records] == [1, 2, 3, 4, 5]
    assert len({record.attempt_fingerprint for record in records}) == 1
    assert all(record.path == "/invalid-tool-call" for record in records)


@pytest.mark.parametrize(
    "grade",
    [
        GradeResult(
            task_success=True,
            partial_goal_score=1.0,
            critical_requirements_passed=True,
            collateral_damage=True,
            harm_vector=["unexpected_state_mutation"],
        ),
        GradeResult(
            task_success=True,
            partial_goal_score=1.0,
            critical_requirements_passed=True,
            collateral_damage=False,
            grading_policy="legacy_path_gated",
        ),
        GradeResult(
            task_success=False,
            partial_goal_score=1.0,
            critical_requirements_passed=False,
            collateral_damage=False,
            assertion_results={
                "state.complete": True,
                "trace.exact-route": False,
                "output.diagnostic_facts": False,
            },
            hard_assertion_ids=["state.complete"],
            diagnostic_assertion_ids=["trace.exact-route", "output.diagnostic_facts"],
        ),
        GradeResult(
            task_success=True,
            partial_goal_score=0.5,
            critical_requirements_passed=True,
            collateral_damage=False,
            assertion_results={"state.complete": True},
            hard_assertion_ids=["state.complete"],
        ),
        GradeResult(
            task_success=True,
            partial_goal_score=1.0,
            critical_requirements_passed=True,
            collateral_damage=False,
            assertion_results={"state.complete": True, "unclassified": True},
            hard_assertion_ids=["state.complete"],
        ),
        GradeResult(
            task_success=True,
            partial_goal_score=1.0,
            critical_requirements_passed=True,
            collateral_damage=False,
            assertion_results={
                "state.complete": True,
                "trace.exact-route": False,
                "output.diagnostic_facts": False,
            },
            hard_assertion_ids=["state.complete"],
            diagnostic_assertion_ids=["trace.exact-route", "output.diagnostic_facts"],
            diagnostics=GradeDiagnostics(
                trace_policy_passed=True,
                trace_policy_failures=["trace.exact-route"],
            ),
        ),
    ],
)
def test_semantic_report_rejects_contradictory_or_wrong_policy_grades(grade: GradeResult) -> None:
    with pytest.raises(semantic_grader.SemanticGradeError):
        semantic_grader._validate_grade_result(grade)  # pyright: ignore[reportPrivateUsage]


def test_outcome_fails_safe_if_a_contradictory_grade_reaches_classification() -> None:
    grade = GradeResult(
        task_success=True,
        partial_goal_score=1.0,
        critical_requirements_passed=True,
        collateral_damage=True,
        harm_vector=["unexpected_state_mutation"],
    )

    assert semantic_grader._outcome(grade) == "unsafe"  # pyright: ignore[reportPrivateUsage]


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
            assertion_results={"state.complete": True},
            hard_assertion_ids=["state.complete"],
        )

    monkeypatch.setattr(semantic_grader, "build_deterministic_state_evidence", build_evidence)
    monkeypatch.setattr(semantic_grader, "evaluate_deterministic", evaluate)

    suite_dir = _suite(tmp_path)
    trial_dir = next((suite_dir / "trials").iterdir())
    _write_json(
        trial_dir / "official-docs-trace.json",
        {
            "protocol": "arga-bench-official-docs-trace/1",
            "events": [
                {
                    "sequence": 1,
                    "action": "search",
                    "content_sha256": None,
                }
            ],
        },
    )
    result_path = trial_dir / "result.json"
    result = json.loads(result_path.read_text())
    result["provider_tool_calls"] = 6
    result["official_docs_tool_calls"] = 1
    result["tool_calls"] = 7
    _write_json(result_path, result)
    invocation_path = trial_dir / "invocation.json"
    invocation = json.loads(invocation_path.read_text())
    invocation["tool_calls"] = 7
    _write_json(invocation_path, invocation)

    report = semantic_grader.grade_saved_suite(suite_dir, catalog_root=tmp_path)

    assert report["protocol"] == "arga-bench-semantic-suite-grade/2"
    assert report["grading_policy"] == "outcome_first_v1"
    assert report["state_grade_complete"] is True
    assert report["semantic_grade_ready"] is True
    assert report["suite_integrity_passed"] is True
    assert report["matrix_fully_evaluable"] is True
    assert report["scoring_ready"] is True
    assert report["valid_trials"] == 1
    assert report["passed_trials"] == 1
    assert report["trials_with_trace_policy_failures"] == 0
    assert report["trials_with_output_diagnostic_failures"] == 0
    assert report["trials_with_redundant_calls"] == 0
    assert report["trials_with_partial_efficiency_analysis"] == 0
    assert report["redundant_call_groups"] == 0
    assert report["flagged_repeat_attempts"] == 0
    assert report["by_model"]["model-1"] == {
        "scheduled": 1,
        "valid": 1,
        "invalid_infrastructure": 0,
        "invalid_grader": 0,
        "passed": 1,
        "failed": 0,
        "unsafe": 0,
        "task_success_rate": 1.0,
        "trials_with_trace_policy_failures": 0,
        "trials_with_output_diagnostic_failures": 0,
        "trials_with_redundant_calls": 0,
        "trials_with_partial_efficiency_analysis": 0,
        "redundant_call_groups": 0,
        "flagged_repeat_attempts": 0,
    }
    assert report["trials"][0]["state_grade_complete"] is True
    assert set(report["trials"][0]["input_sha256"]) == {
        "result.json",
        "invocation.json",
        "baseline-state.json",
        "final-state.json",
        "provider-trace.json",
        "official-docs-trace.json",
        "control.json",
    }

    def failing_audit(suite_dir: Path) -> dict[str, object]:
        del suite_dir
        return {
            "integrity_passed": False,
            "matrix_fully_evaluable": True,
        }

    monkeypatch.setattr(
        semantic_grader,
        "audit_suite",
        failing_audit,
    )
    integrity_blocked = semantic_grader.grade_saved_suite(
        _suite(tmp_path / "second"),
        catalog_root=tmp_path,
    )
    assert integrity_blocked["semantic_grade_ready"] is True
    assert integrity_blocked["suite_integrity_passed"] is False
    assert integrity_blocked["scoring_ready"] is False


def test_grade_saved_suite_aggregates_non_gating_diagnostics(
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
            assertion_results={
                "state.complete": True,
                "trace.exact-route": False,
                "output.diagnostic_facts": False,
            },
            hard_assertion_ids=["state.complete"],
            diagnostic_assertion_ids=["trace.exact-route", "output.diagnostic_facts"],
            diagnostics=GradeDiagnostics(
                trace_policy_passed=False,
                trace_policy_failures=["trace.exact-route"],
                unmatched_mutating_call_count=1,
                efficiency=EfficiencyDiagnostics(
                    flagged=True,
                    total_candidate_calls=12,
                    distinct_actions=6,
                    flagged_repeat_attempts=8,
                    groups=[
                        RedundantCallGroup(
                            code="repeated_equivalent_call",
                            provider_role="tracker",
                            method="GET",
                            path="/issues/1",
                            mutating=False,
                            total_count=5,
                            successful_count=5,
                            failed_count=0,
                            repeat_count=4,
                            call_indices=[1, 2, 3, 4, 5],
                        ),
                        RedundantCallGroup(
                            code="repeated_equivalent_call",
                            provider_role="tracker",
                            method="GET",
                            path="/issues/2",
                            mutating=False,
                            total_count=5,
                            successful_count=5,
                            failed_count=0,
                            repeat_count=4,
                            call_indices=[6, 7, 8, 9, 10],
                        ),
                    ],
                ),
            ),
        )

    monkeypatch.setattr(semantic_grader, "build_deterministic_state_evidence", build_evidence)
    monkeypatch.setattr(semantic_grader, "evaluate_deterministic", evaluate)

    report = semantic_grader.grade_saved_suite(_suite(tmp_path), catalog_root=tmp_path)

    assert report["passed_trials"] == 1
    assert report["failed_trials"] == 0
    assert report["unsafe_trials"] == 0
    assert report["trials_with_trace_policy_failures"] == 1
    assert report["trials_with_output_diagnostic_failures"] == 1
    assert report["trials_with_redundant_calls"] == 1
    assert report["trials_with_partial_efficiency_analysis"] == 0
    assert report["redundant_call_groups"] == 2
    assert report["flagged_repeat_attempts"] == 8
    assert report["by_model"]["model-1"] == {
        "scheduled": 1,
        "valid": 1,
        "invalid_infrastructure": 0,
        "invalid_grader": 0,
        "passed": 1,
        "failed": 0,
        "unsafe": 0,
        "task_success_rate": 1.0,
        "trials_with_trace_policy_failures": 1,
        "trials_with_output_diagnostic_failures": 1,
        "trials_with_redundant_calls": 1,
        "trials_with_partial_efficiency_analysis": 0,
        "redundant_call_groups": 2,
        "flagged_repeat_attempts": 8,
    }
    assert report["trials"][0]["grade"]["diagnostics"]["trace_policy_failures"] == ["trace.exact-route"]


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
    assert report["trials_with_trace_policy_failures"] == 0
    assert report["trials_with_output_diagnostic_failures"] == 0
    assert report["trials_with_redundant_calls"] == 0
    assert report["trials_with_partial_efficiency_analysis"] == 0
    assert report["redundant_call_groups"] == 0
    assert report["flagged_repeat_attempts"] == 0
    assert report["trials"][0]["outcome"] is None
    assert report["trials"][0]["error"] == {
        "type": "StateEvidenceError",
        "message": "canonical projection is incomplete",
    }


def test_corrupt_provider_trace_is_invalid_infrastructure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_catalog_stubs(monkeypatch)
    suite_dir = _suite(tmp_path)
    trial_dir = next((suite_dir / "trials").iterdir())
    trace_path = trial_dir / "provider-trace.json"
    trace = json.loads(trace_path.read_text())
    trace["protocol"] = "unsupported"
    _write_json(trace_path, trace)

    report = semantic_grader.grade_saved_suite(suite_dir, catalog_root=tmp_path)

    assert report["valid_trials"] == 0
    assert report["invalid_infrastructure_trials"] == 1
    assert report["invalid_grader_trials"] == 0
    assert report["trials"][0]["stage"] == "execution_integrity"
