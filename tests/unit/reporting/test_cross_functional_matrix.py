from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest

from arga_twins_benchmark.reporting.cross_functional_matrix import (
    CROSS_FUNCTIONAL_MATRIX_CLASSIFICATION_PROTOCOL,
    CrossFunctionalMatrixClassificationError,
    classify_cross_functional_matrix,
    write_cross_functional_matrix_report,
)

ROOT = Path(__file__).resolve().parents[3]
SUITE_PATH = ROOT / "benchmark" / "cross_functional_40" / "suite.json"
MODEL_MATRIX_PATH = ROOT / "benchmark" / "cross_functional_40" / "model_matrix.json"
CALIBRATION_PATH = (
    ROOT
    / "benchmark"
    / "cross_functional_40"
    / "historical_fable_5_high_fairness_calibration.json"
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _content_hash(task: dict[str, Any]) -> str:
    payload = json.dumps(task, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _fixture_root(tmp_path: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    model_matrix = json.loads(MODEL_MATRIX_PATH.read_text(encoding="utf-8"))
    matrix_dir = tmp_path / "matrix"
    _write_json(
        matrix_dir / "matrix-config.json",
        {
            "protocol": "arga-bench-cross-functional-model-matrix-run/1",
            "suite_id": suite["suite_id"],
            "profiles": model_matrix["profiles"],
            "profile_count": 30,
            "scenarios_per_profile": 40,
            "total_trials": 1200,
            "attempts_per_model_scenario_pair": 1,
        },
    )
    profile = next(item for item in model_matrix["profiles"] if item["id"] == "fable-5-low")
    profile_dir = matrix_dir / "profiles" / profile["id"]
    _write_json(
        profile_dir / "run-config.json",
        {
            "protocol": "arga-bench-cross-functional-run/2",
            "suite_id": suite["suite_id"],
            "profile": profile,
            "attempts_per_scenario": 1,
        },
    )
    _write_json(
        profile_dir / "staging-scenarios.json",
        {
            "suite_tag": "suite:cross-functional-40-v1",
            "scenario_ids": {task["id"]: f"scenario-{task['id'].lower()}" for task in suite["tasks"]},
        },
    )
    return matrix_dir, suite, profile


def _write_attempt(
    matrix_dir: Path,
    *,
    task: dict[str, Any],
    profile: dict[str, Any],
    status: str,
    cleanup_ok: bool = True,
    invocation_effort: str | None = None,
    attempt_prompt: str | None = None,
    queries: dict[str, Any] | None = None,
    attempt_number: int = 1,
) -> None:
    task_id = task["id"]
    task_dir = matrix_dir / "profiles" / profile["id"] / "tasks" / task_id
    run_id = f"run-{task_id.lower()}"
    scenario_id = f"scenario-{task_id.lower()}"
    stop_reason = {
        "completed": "end_turn",
        "timed_out": "timeout",
        "tool_limit_exceeded": "tool_limit_exceeded",
        "refused": "refusal",
        "api_error": "http_429",
    }[status]
    final_text = "done" if status == "completed" else ""
    user_prompt = task["prompt"]
    recorded_prompt = attempt_prompt if attempt_prompt is not None else user_prompt
    attempt_status = "infrastructure_invalid" if status == "api_error" else "candidate_complete"
    usage = {"input_tokens": 10, "output_tokens": 1}
    _write_json(
        task_dir / "attempt.json",
        {
            "protocol": "arga-bench-cross-functional-attempt/2",
            "attempt_number": attempt_number,
            "task_id": task_id,
            "scenario_id": scenario_id,
            "run_id": run_id,
            "profile_id": profile["id"],
            "model_label": profile["label"],
            "model": profile["model_id"],
            "provider": profile["provider"],
            "requested_effort": profile["requested_effort"],
            "api_effort": profile["api_effort"],
            "thinking": profile["thinking"],
            "attempt_status": attempt_status,
            "model_status": status,
            "response_model": profile["model_id"],
            "stop_reason": stop_reason,
            "prompt": recorded_prompt,
            "prompt_sha256": hashlib.sha256(user_prompt.encode()).hexdigest(),
            "prompt_matches_tasks_md": True,
            "seed_status": "ready",
            "final_text": final_text,
            "usage": usage,
            "output_tokens": 1,
            "tool_calls": 0,
            "provider_tool_calls": 0,
            "official_docs_tool_calls": 0,
            "raw_state_delta_count": 0,
            "cleanup_succeeded": cleanup_ok,
        },
    )
    _write_json(
        task_dir / "control.json",
        {
            "protocol": "arga-bench-control/1",
            "instance_id": task_id,
            "scenario_id": scenario_id,
            "scenario_content_sha256": _content_hash(task),
            "run_id": run_id,
            "twin_run": {"run_id": run_id, "status": "ready", "twins": {}},
        },
    )
    _write_json(
        task_dir / "cleanup.json",
        {
            "twin_run": {
                "run_id": run_id,
                "status": "torn_down",
                "twins": {} if cleanup_ok else {"still-live": {}},
            }
        },
    )
    _write_json(
        task_dir / "prompt.json",
        {
            "protocol": "arga-bench-trial-prompt/1",
            "profile_id": profile["id"],
            "model": profile["model_id"],
            "requested_effort": profile["requested_effort"],
            "api_effort": profile["api_effort"],
            "thinking": profile["thinking"],
            "system_prompt": "system",
            "user_prompt": user_prompt,
        },
    )
    _write_json(
        task_dir / "invocation.json",
        {
            "requested_model": profile["model_id"],
            "response_model": profile["model_id"],
            "provider": profile["provider"],
            "status": status,
            "stop_reason": stop_reason,
            "system_prompt": "system",
            "user_prompt": user_prompt,
            "final_text": final_text,
            "usage": usage,
            "tool_calls": 0,
            "config": {
                "model": profile["model_id"],
                "provider": profile["provider"],
                "effort": invocation_effort or profile["api_effort"],
                "thinking": {"type": profile["thinking"]},
            },
        },
    )
    snapshot: dict[str, Any] = {
        "providers": {provider: {} for provider in cast(list[str], task["twins"])},
        "queries": {} if queries is None else queries,
    }
    _write_json(task_dir / "baseline-state.json", snapshot)
    _write_json(task_dir / "final-state.json", snapshot)
    _write_json(
        task_dir / "raw-state-diff.json",
        {"protocol": "arga-bench-raw-state-diff/1", "deltas": []},
    )
    _write_json(
        task_dir / "provider-trace.json",
        {"protocol": "arga-bench-provider-trace/1", "events": []},
    )
    _write_json(
        task_dir / "official-docs-trace.json",
        {"protocol": "arga-bench-official-docs-trace/1", "events": []},
    )
    _write_json(
        task_dir / "tool-steps.json",
        {"protocol": "arga-bench-tool-steps/1", "steps": []},
    )


def _classify(matrix_dir: Path) -> dict[str, Any]:
    return classify_cross_functional_matrix(
        matrix_dir,
        suite_path=SUITE_PATH,
        model_matrix_path=MODEL_MATRIX_PATH,
        historical_calibration_path=CALIBRATION_PATH,
    )


def test_offline_classifier_separates_execution_terminal_and_fail_closed_evidence(
    tmp_path: Path,
) -> None:
    matrix_dir, suite, profile = _fixture_root(tmp_path)
    statuses = ("completed", "timed_out", "tool_limit_exceeded", "refused")
    for index, (task, status) in enumerate(zip(suite["tasks"][:4], statuses, strict=True)):
        _write_attempt(
            matrix_dir,
            task=task,
            profile=profile,
            status=status,
            attempt_number=2 if index == 0 else 1,
        )
    _write_attempt(matrix_dir, task=suite["tasks"][4], profile=profile, status="api_error")
    _write_attempt(
        matrix_dir,
        task=suite["tasks"][5],
        profile=profile,
        status="completed",
        cleanup_ok=False,
    )
    _write_attempt(
        matrix_dir,
        task=suite["tasks"][6],
        profile=profile,
        status="completed",
        invocation_effort="high",
    )
    _write_attempt(
        matrix_dir,
        task=suite["tasks"][7],
        profile=profile,
        status="completed",
        attempt_prompt="tampered prompt",
    )

    report = _classify(matrix_dir)

    assert report["protocol"] == CROSS_FUNCTIONAL_MATRIX_CLASSIFICATION_PROTOCOL
    by_task = {
        item["task_id"]: item
        for item in report["attempts"]
        if item["profile_id"] == profile["id"]
    }
    assert by_task["IT-01"]["execution_class"] == "exact_completed"
    assert by_task["IT-01"]["validity"] == "invalid_grader"
    assert by_task["IT-01"]["evidence_gaps"] == ["missing_snapshot_query_evidence"]
    assert by_task["IT-02"]["model_terminal_reason"] == "timed_out"
    assert by_task["IT-03"]["model_terminal_reason"] == "tool_limit_exceeded"
    assert by_task["IT-04"]["model_terminal_reason"] == "refused"
    assert by_task["IT-05"]["execution_class"] == "infrastructure_invalid"
    assert "model_infrastructure_status:api_error" in by_task["IT-05"]["integrity"]["issues"]
    assert "cleanup:inert_twin_not_proven" in by_task["IT-06"]["integrity"]["issues"]
    assert "invocation_config:mismatched_effort" in by_task["IT-07"]["integrity"]["issues"]
    assert "attempt:mismatched_prompt" in by_task["IT-08"]["integrity"]["issues"]

    aggregate = report["profiles"][profile["id"]]
    assert aggregate["execution"] == {
        "exact_completed": 1,
        "model_terminal": 3,
        "model_terminal_by_reason": {
            "refused": 1,
            "timed_out": 1,
            "tool_limit_exceeded": 1,
        },
        "infrastructure_invalid": 36,
        "execution_completion_denominator": 4,
        "exact_completion_rate": 0.25,
    }
    assert aggregate["validity"] == {
        "valid": 0,
        "invalid_infrastructure": 36,
        "invalid_grader": 4,
    }
    assert aggregate["scoring"]["denominator"] == 0
    assert aggregate["scoring"]["pass_rate"] is None
    assert aggregate["scoring"]["excluded_invalid_infrastructure"] == 36
    assert aggregate["scoring"]["excluded_invalid_grader"] == 4


def test_historical_fable_high_verdicts_are_referenced_but_never_applied(tmp_path: Path) -> None:
    matrix_dir, suite, profile = _fixture_root(tmp_path)
    _write_attempt(matrix_dir, task=suite["tasks"][0], profile=profile, status="completed")

    report = _classify(matrix_dir)

    calibration = report["historical_calibration"]
    assert calibration["historical_passes"] == 21
    assert calibration["historical_failures"] == 19
    assert calibration["profile"]["requested_effort"] == "high"
    assert calibration["matching_current_profile_ids"] == []
    assert calibration["applied_to_current_attempts"] is False
    assert calibration["application_count"] == 0
    assert all(item["historical_calibration_applied"] is False for item in report["attempts"])
    assert all(item["semantic_outcome"] is None for item in report["attempts"])


def test_nonempty_queries_remain_invalid_until_executable_task_verifiers_exist(tmp_path: Path) -> None:
    matrix_dir, suite, profile = _fixture_root(tmp_path)
    _write_attempt(
        matrix_dir,
        task=suite["tasks"][0],
        profile=profile,
        status="completed",
        queries={"task-query": {"status_code": 200, "payload": {}}},
    )

    report = _classify(matrix_dir)
    attempt = next(item for item in report["attempts"] if item["trial_id"] == "fable-5-low/IT-01")

    assert attempt["execution_class"] == "exact_completed"
    assert attempt["validity"] == "invalid_grader"
    assert attempt["evidence_gaps"] == ["semantic_grade_not_applied_by_integrity_classifier"]
    assert attempt["score_eligible"] is False


def test_legacy_attempt_number_is_accepted_only_with_unambiguous_zero_invocation_history(
    tmp_path: Path,
) -> None:
    matrix_dir, suite, profile = _fixture_root(tmp_path)
    profile_dir = matrix_dir / "profiles" / profile["id"]

    missing_number = suite["tasks"][0]
    _write_attempt(matrix_dir, task=missing_number, profile=profile, status="completed")
    attempt_path = profile_dir / "tasks" / missing_number["id"] / "attempt.json"
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    attempt.pop("attempt_number")
    _write_json(attempt_path, attempt)

    interrupted_retry = suite["tasks"][1]
    _write_attempt(
        matrix_dir,
        task=interrupted_retry,
        profile=profile,
        status="completed",
        attempt_number=2,
    )
    interrupted_archive = profile_dir / "retry-archive" / interrupted_retry["id"] / "attempt-0001"
    _write_json(
        interrupted_archive / "archive-metadata.json",
        {
            "protocol": "arga-bench-cross-functional-retry-archive/1",
            "archive_number": 1,
            "archive_reason": "interrupted_before_attempt",
            "profile_id": profile["id"],
            "task_id": interrupted_retry["id"],
        },
    )

    zero_invocation_retry = suite["tasks"][2]
    _write_attempt(
        matrix_dir,
        task=zero_invocation_retry,
        profile=profile,
        status="completed",
        attempt_number=2,
    )
    zero_archive = profile_dir / "retry-archive" / zero_invocation_retry["id"] / "attempt-0001"
    _write_json(
        zero_archive / "archive-metadata.json",
        {
            "protocol": "arga-bench-cross-functional-retry-archive/1",
            "archive_number": 1,
            "archive_reason": "zero_invocation_infrastructure_invalid",
            "profile_id": profile["id"],
            "task_id": zero_invocation_retry["id"],
        },
    )
    _write_json(
        zero_archive / "attempt.json",
        {
            "protocol": "arga-bench-cross-functional-attempt/2",
            "attempt_status": "infrastructure_invalid",
            "profile_id": profile["id"],
            "task_id": zero_invocation_retry["id"],
            "model_status": None,
            "final_text": "",
            "tool_calls": 0,
            "provider_tool_calls": 0,
            "official_docs_tool_calls": 0,
        },
    )

    ambiguous_retry = suite["tasks"][3]
    _write_attempt(
        matrix_dir,
        task=ambiguous_retry,
        profile=profile,
        status="completed",
        attempt_number=2,
    )
    ambiguous_archive = profile_dir / "retry-archive" / ambiguous_retry["id"] / "attempt-0001"
    _write_json(
        ambiguous_archive / "archive-metadata.json",
        {
            "protocol": "arga-bench-cross-functional-retry-archive/1",
            "archive_number": 1,
            "archive_reason": "interrupted_before_attempt",
            "profile_id": profile["id"],
            "task_id": ambiguous_retry["id"],
        },
    )
    _write_json(ambiguous_archive / "invocation.json", {"status": "completed"})

    terminal_missing_number = suite["tasks"][4]
    _write_attempt(matrix_dir, task=terminal_missing_number, profile=profile, status="timed_out")
    terminal_path = profile_dir / "tasks" / terminal_missing_number["id"] / "attempt.json"
    terminal_attempt = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal_attempt.pop("attempt_number")
    _write_json(terminal_path, terminal_attempt)

    invalid_zero_number = suite["tasks"][5]
    _write_attempt(
        matrix_dir,
        task=invalid_zero_number,
        profile=profile,
        status="completed",
        attempt_number=0,
    )

    report = _classify(matrix_dir)
    by_task = {item["task_id"]: item for item in report["attempts"] if item["profile_id"] == profile["id"]}

    for task in (missing_number, interrupted_retry, zero_invocation_retry):
        assert by_task[task["id"]]["execution_class"] == "exact_completed"
        assert "attempt:invalid_attempt_number" not in by_task[task["id"]]["integrity"]["issues"]
    assert by_task[ambiguous_retry["id"]]["execution_class"] == "infrastructure_invalid"
    assert "attempt:invalid_attempt_number" in by_task[ambiguous_retry["id"]]["integrity"]["issues"]
    assert by_task[terminal_missing_number["id"]]["execution_class"] == "model_terminal"
    assert "attempt:invalid_attempt_number" not in by_task[terminal_missing_number["id"]]["integrity"]["issues"]
    assert by_task[invalid_zero_number["id"]]["execution_class"] == "infrastructure_invalid"
    assert "attempt:invalid_attempt_number" in by_task[invalid_zero_number["id"]]["integrity"]["issues"]


def test_explicit_infrastructure_retry_archive_accepts_only_non_scoring_model_statuses(
    tmp_path: Path,
) -> None:
    matrix_dir, suite, profile = _fixture_root(tmp_path)
    profile_dir = matrix_dir / "profiles" / profile["id"]

    safe_retry = suite["tasks"][0]
    _write_attempt(matrix_dir, task=safe_retry, profile=profile, status="completed", attempt_number=2)
    safe_archive = profile_dir / "retry-archive" / safe_retry["id"] / "attempt-0001"
    _write_json(
        safe_archive / "archive-metadata.json",
        {
            "protocol": "arga-bench-cross-functional-retry-archive/1",
            "archive_number": 1,
            "archive_reason": "explicit_model_infrastructure_retry",
            "profile_id": profile["id"],
            "task_id": safe_retry["id"],
            "cleanup": {"confirmation": {"outcome": "terminal_without_twins"}},
        },
    )
    _write_json(
        safe_archive / "attempt.json",
        {
            "protocol": "arga-bench-cross-functional-attempt/2",
            "attempt_status": "infrastructure_invalid",
            "profile_id": profile["id"],
            "task_id": safe_retry["id"],
            "model_status": "api_error",
        },
    )
    _write_json(safe_archive / "invocation.json", {"status": "api_error"})

    unsafe_retry = suite["tasks"][1]
    _write_attempt(matrix_dir, task=unsafe_retry, profile=profile, status="completed", attempt_number=2)
    unsafe_archive = profile_dir / "retry-archive" / unsafe_retry["id"] / "attempt-0001"
    _write_json(
        unsafe_archive / "archive-metadata.json",
        {
            "protocol": "arga-bench-cross-functional-retry-archive/1",
            "archive_number": 1,
            "archive_reason": "explicit_model_infrastructure_retry",
            "profile_id": profile["id"],
            "task_id": unsafe_retry["id"],
            "cleanup": {"confirmation": {"outcome": "terminal_without_twins"}},
        },
    )
    _write_json(
        unsafe_archive / "attempt.json",
        {
            "protocol": "arga-bench-cross-functional-attempt/2",
            "attempt_status": "infrastructure_invalid",
            "profile_id": profile["id"],
            "task_id": unsafe_retry["id"],
            "model_status": "completed",
        },
    )
    _write_json(unsafe_archive / "invocation.json", {"status": "completed"})

    report = _classify(matrix_dir)
    by_task = {item["task_id"]: item for item in report["attempts"] if item["profile_id"] == profile["id"]}

    assert "attempt:invalid_attempt_number" not in by_task[safe_retry["id"]]["integrity"]["issues"]
    assert "attempt:invalid_attempt_number" in by_task[unsafe_retry["id"]]["integrity"]["issues"]


def test_report_writer_refuses_to_mutate_preserved_matrix(tmp_path: Path) -> None:
    matrix_dir, suite, profile = _fixture_root(tmp_path)
    _write_attempt(matrix_dir, task=suite["tasks"][0], profile=profile, status="completed")
    report = _classify(matrix_dir)

    with pytest.raises(CrossFunctionalMatrixClassificationError, match="inside the preserved matrix run"):
        write_cross_functional_matrix_report(
            report,
            matrix_dir / "offline-report.json",
            source_matrix_dir=matrix_dir,
        )

    output = tmp_path / "reports" / "offline-report.json"
    write_cross_functional_matrix_report(report, output, source_matrix_dir=matrix_dir)
    assert json.loads(output.read_text(encoding="utf-8"))["protocol"] == (
        CROSS_FUNCTIONAL_MATRIX_CLASSIFICATION_PROTOCOL
    )
