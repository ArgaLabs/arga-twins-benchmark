from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "scripts" / "run_cross_functional_40.py"
SPEC = importlib.util.spec_from_file_location("cross_functional_40_resume_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)

MATRIX_RUNNER_PATH = ROOT / "scripts" / "run_cross_functional_model_matrix.py"
MATRIX_SPEC = importlib.util.spec_from_file_location("cross_functional_model_matrix_resume_runner", MATRIX_RUNNER_PATH)
assert MATRIX_SPEC is not None and MATRIX_SPEC.loader is not None
matrix_runner = importlib.util.module_from_spec(MATRIX_SPEC)
sys.modules[MATRIX_SPEC.name] = matrix_runner
MATRIX_SPEC.loader.exec_module(matrix_runner)


PROFILE_ID = "gpt-5-6-luna-light"
TASK_ID = "it-01"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _zero_invocation_attempt(*, status: str = "infrastructure_invalid") -> dict[str, Any]:
    return {
        "protocol": "arga-bench-cross-functional-attempt/2",
        "task_id": TASK_ID,
        "profile_id": PROFILE_ID,
        "attempt_status": status,
        "model_status": None,
        "response_model": None,
        "stop_reason": None,
        "final_text": "",
        "usage": {},
        "output_tokens": 0,
        "tool_calls": 0,
        "provider_tool_calls": 0,
        "official_docs_tool_calls": 0,
    }


def _inert_cleanup(run_id: str = "run-1") -> dict[str, Any]:
    return {
        "twin_run": {"run_id": run_id, "status": "cancelled", "twins": {}},
        "confirmation": {"outcome": "terminal_without_twins", "confirmed_status": "cancelled"},
    }


def _terminal_attempt(status: str, *, attempt_number: int = 1) -> dict[str, Any]:
    return {
        **_zero_invocation_attempt(status="candidate_complete"),
        "attempt_number": attempt_number,
        "run_id": "run-1",
        "model_status": status,
        "response_model": "gpt-5.6-luna",
        "stop_reason": status,
    }


def test_resume_allows_only_zero_invocation_infrastructure_invalid_attempt(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    _write_json(task_dir / "attempt.json", _zero_invocation_attempt())

    decision = runner.classify_resume_task(task_dir, task_id=TASK_ID, profile_id=PROFILE_ID)

    assert decision.action == "run"
    assert decision.reason == "zero_invocation_infrastructure_invalid"


def test_resume_never_replays_pair_with_invocation_artifact(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    _write_json(task_dir / "attempt.json", _zero_invocation_attempt())
    # Existence is the boundary; even malformed or incomplete invocation evidence is protected.
    (task_dir / "invocation.json").write_text("partial")

    decision = runner.classify_resume_task(task_dir, task_id=TASK_ID, profile_id=PROFILE_ID)

    assert decision.action == "skip"
    assert decision.reason == "model_invocation_protected"


def test_explicit_infrastructure_retry_archives_api_error_but_not_completed_trial(
    tmp_path: Path,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    api_error_attempt = {
        **_zero_invocation_attempt(),
        "model_status": "api_error",
        "response_model": "gpt-5.6-luna",
        "stop_reason": "http_429",
    }
    _write_json(task_dir / "attempt.json", api_error_attempt)
    _write_json(task_dir / "invocation.json", {"status": "api_error"})

    default_decision = runner.classify_resume_task(
        task_dir,
        task_id=TASK_ID,
        profile_id=PROFILE_ID,
    )
    retry_decision = runner.classify_resume_task(
        task_dir,
        task_id=TASK_ID,
        profile_id=PROFILE_ID,
        retry_infrastructure_invalid=True,
    )

    assert default_decision.reason == "model_invocation_protected"
    assert retry_decision.action == "run"
    assert retry_decision.reason == "explicit_model_infrastructure_retry"

    _write_json(
        task_dir / "attempt.json",
        {
            **api_error_attempt,
            "attempt_status": "candidate_complete",
            "model_status": "invalid_response",
            "stop_reason": "tool_use_without_calls",
        },
    )
    _write_json(task_dir / "invocation.json", {"status": "invalid_response"})
    invalid_response_retry = runner.classify_resume_task(
        task_dir,
        task_id=TASK_ID,
        profile_id=PROFILE_ID,
        retry_infrastructure_invalid=True,
    )
    assert invalid_response_retry.reason == "explicit_model_infrastructure_retry"

    _write_json(
        task_dir / "attempt.json",
        {
            **api_error_attempt,
            "attempt_status": "candidate_complete",
            "model_status": "completed",
            "stop_reason": "completed",
        },
    )
    _write_json(task_dir / "invocation.json", {"status": "completed"})
    protected = runner.classify_resume_task(
        task_dir,
        task_id=TASK_ID,
        profile_id=PROFILE_ID,
        retry_infrastructure_invalid=True,
    )
    assert protected.action == "skip"
    assert protected.reason == "model_invocation_protected"


def test_explicit_infrastructure_retry_accepts_completed_model_with_failed_evidence_capture(
    tmp_path: Path,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    _write_json(
        task_dir / "attempt.json",
        {
            **_zero_invocation_attempt(),
            "run_id": "run-1",
            "model_status": "completed",
            "response_model": "gpt-5.6-luna",
            "stop_reason": "completed",
            "error_type": "_StateCaptureHttpError",
            "error": "snapshot query returned HTTP 500",
        },
    )
    _write_json(task_dir / "invocation.json", {"status": "completed"})
    _write_json(task_dir / "control.json", {"scenario_id": "scenario-1", "run_id": "run-1"})
    _write_json(task_dir / "cleanup.json", _inert_cleanup())

    plan, decision, _cleanup = asyncio.run(
        runner.prepare_resume_task(
            output_root=tmp_path,
            task={"id": TASK_ID},
            profile_id=PROFILE_ID,
            semaphore=asyncio.Semaphore(1),
            retry_infrastructure_invalid=True,
        )
    )

    archive = tmp_path / runner.RETRY_ARCHIVE_DIR / TASK_ID / "attempt-0001"
    assert plan == runner.TaskRunPlan({"id": TASK_ID}, 2, str(archive))
    assert decision.reason == "explicit_post_invocation_infrastructure_retry"
    assert json.loads((archive / "invocation.json").read_text())["status"] == "completed"


@pytest.mark.parametrize("artifact", [runner.INVOCATION_STARTED_ARTIFACT, "baseline-state.json"])
def test_explicit_infrastructure_retry_protects_ambiguous_crash_window(
    tmp_path: Path,
    artifact: str,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    _write_json(task_dir / artifact, {"recorded": True})

    decision = runner.classify_resume_task(
        task_dir,
        task_id=TASK_ID,
        profile_id=PROFILE_ID,
        retry_infrastructure_invalid=True,
    )

    assert decision.action == "skip"
    assert decision.reason == "model_invocation_protected"


def test_explicit_infrastructure_retry_preserves_archived_invocation(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    _write_json(
        task_dir / "attempt.json",
        {
            **_zero_invocation_attempt(),
            "run_id": "run-1",
            "model_status": "api_error",
            "response_model": "gpt-5.6-luna",
            "stop_reason": "transport_error",
        },
    )
    _write_json(task_dir / "invocation.json", {"status": "api_error"})
    _write_json(task_dir / "control.json", {"scenario_id": "scenario-1", "run_id": "run-1"})
    _write_json(task_dir / "cleanup.json", _inert_cleanup())

    plan, decision, _cleanup = asyncio.run(
        runner.prepare_resume_task(
            output_root=tmp_path,
            task={"id": TASK_ID},
            profile_id=PROFILE_ID,
            semaphore=asyncio.Semaphore(1),
            retry_infrastructure_invalid=True,
        )
    )

    archive = tmp_path / runner.RETRY_ARCHIVE_DIR / TASK_ID / "attempt-0001"
    assert plan == runner.TaskRunPlan({"id": TASK_ID}, 2, str(archive))
    assert decision.reason == "explicit_model_infrastructure_retry"
    assert json.loads((archive / "invocation.json").read_text())["status"] == "api_error"
    metadata = json.loads((archive / "archive-metadata.json").read_text())
    assert metadata["archive_reason"] == "explicit_model_infrastructure_retry"
    assert metadata["cleanup"] == _inert_cleanup()


@pytest.mark.parametrize("status", ["tool_limit_exceeded", "timed_out", "refused"])
def test_explicit_terminal_retry_archives_first_attempt_once(tmp_path: Path, status: str) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    _write_json(task_dir / "attempt.json", _terminal_attempt(status))
    _write_json(task_dir / "invocation.json", {"status": status})
    _write_json(task_dir / "control.json", {"scenario_id": "scenario-1", "run_id": "run-1"})
    _write_json(task_dir / "cleanup.json", _inert_cleanup())

    plan, decision, _cleanup = asyncio.run(
        runner.prepare_resume_task(
            output_root=tmp_path,
            task={"id": TASK_ID},
            profile_id=PROFILE_ID,
            semaphore=asyncio.Semaphore(1),
            retry_model_terminal=True,
        )
    )

    archive = tmp_path / runner.RETRY_ARCHIVE_DIR / TASK_ID / "attempt-0001"
    assert plan == runner.TaskRunPlan({"id": TASK_ID}, 2, str(archive))
    assert decision.reason == "explicit_model_terminal_retry"
    assert json.loads((archive / "attempt.json").read_text())["model_status"] == status


def test_explicit_terminal_retry_never_replays_second_terminal_attempt(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    _write_json(task_dir / "attempt.json", _terminal_attempt("timed_out", attempt_number=2))
    _write_json(task_dir / "invocation.json", {"status": "timed_out"})
    _write_json(
        tmp_path / runner.RETRY_ARCHIVE_DIR / TASK_ID / "attempt-0001" / "attempt.json",
        _terminal_attempt("timed_out"),
    )
    _write_json(
        tmp_path / runner.RETRY_ARCHIVE_DIR / TASK_ID / "attempt-0001" / "invocation.json",
        {"status": "timed_out"},
    )

    decision = runner.classify_resume_task(
        task_dir,
        task_id=TASK_ID,
        profile_id=PROFILE_ID,
        retry_model_terminal=True,
    )

    assert decision.action == "skip"
    assert decision.reason == "model_terminal_retry_exhausted"


def test_explicit_missing_snapshot_retry_accepts_only_both_empty_query_sets(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    completed = {
        **_zero_invocation_attempt(status="candidate_complete"),
        "model_status": "completed",
        "response_model": "gpt-5.6-luna",
        "stop_reason": "completed",
    }
    _write_json(task_dir / "attempt.json", completed)
    _write_json(task_dir / "invocation.json", {"status": "completed"})
    _write_json(task_dir / "baseline-state.json", {"providers": {}, "queries": {}})
    _write_json(task_dir / "final-state.json", {"providers": {}, "queries": {}})

    decision = runner.classify_resume_task(
        task_dir,
        task_id=TASK_ID,
        profile_id=PROFILE_ID,
        retry_missing_snapshot_evidence=True,
        expected_snapshot_query_ids=frozenset({"it_01_slack_state"}),
    )

    assert decision.action == "run"
    assert decision.reason == "explicit_missing_snapshot_evidence_retry"

    _write_json(
        task_dir / "final-state.json",
        {"providers": {}, "queries": {"it_01_slack_state": {}}},
    )
    partial = runner.classify_resume_task(
        task_dir,
        task_id=TASK_ID,
        profile_id=PROFILE_ID,
        retry_missing_snapshot_evidence=True,
        expected_snapshot_query_ids=frozenset({"it_01_slack_state"}),
    )
    assert partial.action == "blocked"
    assert partial.reason == "snapshot_query_set_partial_or_changed"


def test_archive_rechecks_invocation_boundary_before_move(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    _write_json(task_dir / "attempt.json", _zero_invocation_attempt())
    decision = runner.classify_resume_task(task_dir, task_id=TASK_ID, profile_id=PROFILE_ID)
    _write_json(task_dir / "invocation.json", {"status": "completed"})

    with pytest.raises(RuntimeError, match="resume eligibility changed"):
        runner.archive_retryable_task(
            output_root=tmp_path,
            task_id=TASK_ID,
            profile_id=PROFILE_ID,
            decision=decision,
            cleanup=None,
        )

    assert task_dir.is_dir()
    assert not (tmp_path / runner.RETRY_ARCHIVE_DIR).exists()


def test_resume_protects_started_or_ambiguous_legacy_invocation(tmp_path: Path) -> None:
    marked = tmp_path / "tasks" / "marked"
    _write_json(marked / "attempt.json", {**_zero_invocation_attempt(), "task_id": "marked"})
    _write_json(marked / runner.INVOCATION_STARTED_ARTIFACT, {"started_at": "2030-01-01T00:00:00Z"})
    assert runner.classify_resume_task(marked, task_id="marked", profile_id=PROFILE_ID).action == "skip"

    legacy = tmp_path / "tasks" / "legacy"
    _write_json(legacy / "attempt.json", {**_zero_invocation_attempt(), "task_id": "legacy"})
    _write_json(legacy / "baseline-state.json", {"captured": True})
    decision = runner.classify_resume_task(legacy, task_id="legacy", profile_id=PROFILE_ID)
    assert decision.action == "skip"
    assert decision.reason == "model_invocation_protected"


def test_resume_archives_invalid_attempt_without_rewriting_it(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    original = _zero_invocation_attempt()
    _write_json(task_dir / "attempt.json", original)
    _write_json(task_dir / "control.json", {"scenario_id": "scenario-1", "run_id": "run-1"})
    _write_json(task_dir / "cleanup.json", _inert_cleanup())

    plan, decision, cleanup = asyncio.run(
        runner.prepare_resume_task(
            output_root=tmp_path,
            task={"id": TASK_ID},
            profile_id=PROFILE_ID,
            semaphore=asyncio.Semaphore(1),
        )
    )

    archive = tmp_path / runner.RETRY_ARCHIVE_DIR / TASK_ID / "attempt-0001"
    assert plan == runner.TaskRunPlan({"id": TASK_ID}, 2, str(archive))
    assert decision.reason == "zero_invocation_infrastructure_invalid"
    assert cleanup == _inert_cleanup()
    assert json.loads((archive / "attempt.json").read_text()) == original
    assert json.loads((archive / "archive-metadata.json").read_text())["archive_reason"] == (
        "zero_invocation_infrastructure_invalid"
    )
    assert not task_dir.exists()


def test_resume_archives_missing_attempt_only_after_prior_twin_is_inert(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    _write_json(task_dir / "control.json", {"scenario_id": "scenario-1", "run_id": "run-1"})
    _write_json(task_dir / "cleanup.json", _inert_cleanup())

    plan, decision, _cleanup = asyncio.run(
        runner.prepare_resume_task(
            output_root=tmp_path,
            task={"id": TASK_ID},
            profile_id=PROFILE_ID,
            semaphore=asyncio.Semaphore(1),
        )
    )

    assert plan is not None
    assert plan.attempt_number == 2
    assert decision.reason == "interrupted_before_attempt"
    assert (tmp_path / runner.RETRY_ARCHIVE_DIR / TASK_ID / "attempt-0001" / "control.json").is_file()


def test_resume_continues_attempt_number_when_prior_archive_exists_but_task_never_relaunched(
    tmp_path: Path,
) -> None:
    archive = tmp_path / runner.RETRY_ARCHIVE_DIR / TASK_ID / "attempt-0001"
    _write_json(archive / "archive-metadata.json", {"archive_number": 1})

    plan, decision, cleanup = asyncio.run(
        runner.prepare_resume_task(
            output_root=tmp_path,
            task={"id": TASK_ID},
            profile_id=PROFILE_ID,
            semaphore=asyncio.Semaphore(1),
            retry_infrastructure_invalid=True,
        )
    )

    assert decision.reason == "task_not_started"
    assert cleanup is None
    assert plan == runner.TaskRunPlan({"id": TASK_ID}, 2, str(archive))


def test_resume_blocks_retry_when_prior_twin_cannot_be_identified(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    _write_json(task_dir / "attempt.json", {**_zero_invocation_attempt(), "run_id": "run-unknown"})

    plan, decision, cleanup = asyncio.run(
        runner.prepare_resume_task(
            output_root=tmp_path,
            task={"id": TASK_ID},
            profile_id=PROFILE_ID,
            semaphore=asyncio.Semaphore(1),
        )
    )

    assert plan is None
    assert decision.action == "blocked"
    assert decision.reason == "prior_twin_not_proven_inert"
    assert cleanup is not None and cleanup["error_type"] == "UnsafeResumeBlocked"
    assert (task_dir / "resume-blocked.json").is_file()
    assert not (tmp_path / runner.RETRY_ARCHIVE_DIR).exists()


def test_resume_allows_failed_provision_with_explicitly_unallocated_run(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    _write_json(task_dir / "attempt.json", {**_zero_invocation_attempt(), "run_id": None})

    plan, decision, cleanup = asyncio.run(
        runner.prepare_resume_task(
            output_root=tmp_path,
            task={"id": TASK_ID},
            profile_id=PROFILE_ID,
            semaphore=asyncio.Semaphore(1),
        )
    )

    assert plan is not None and plan.attempt_number == 2
    assert decision.reason == "zero_invocation_infrastructure_invalid"
    assert cleanup is not None and cleanup["outcome"] == "no_run_allocated"


def test_resume_history_records_requested_concurrency(tmp_path: Path) -> None:
    decisions = [(TASK_ID, runner.ResumeDecision("run", "task_not_started"))]
    plans = [runner.TaskRunPlan({"id": TASK_ID}, 1)]

    runner._write_resume_history(
        tmp_path,
        concurrency=7,
        retry_infrastructure_invalid=True,
        retry_model_terminal=True,
        retry_missing_snapshot_evidence=True,
        decisions=decisions,
        plans=plans,
    )

    history = json.loads((tmp_path / "resume-history.json").read_text())
    assert history["entries"][0]["concurrency"] == 7
    assert history["entries"][0]["retry_infrastructure_invalid"] is True
    assert history["entries"][0]["retry_model_terminal"] is True
    assert history["entries"][0]["retry_missing_snapshot_evidence"] is True
    assert history["entries"][0]["candidate_limits"] == {
        "provider_tool_calls": 100,
        "official_docs_tool_calls": 20,
        "total_tool_calls": 120,
        "model_timeout_seconds": 1800,
    }
    assert history["entries"][0]["rerun_tasks"] == [TASK_ID]


def test_matrix_resume_keeps_global_concurrency_bounded(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    active = 0
    maximum_active = 0
    commands: list[tuple[str, ...]] = []

    class FakeProcess:
        async def wait(self) -> int:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return 0

    async def fake_create_subprocess_exec(*command: str, **_kwargs: object) -> FakeProcess:
        commands.append(command)
        return FakeProcess()

    monkeypatch.setattr(matrix_runner.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    output_root = tmp_path / "matrix"
    log_root = output_root / "logs"
    log_root.mkdir(parents=True)
    (output_root / "profiles").mkdir()
    profiles = [
        {
            "id": f"profile-{index}",
            "provider": "openai",
            "model_id": "gpt-test",
            "requested_effort": "low",
            "api_effort": "low",
        }
        for index in range(4)
    ]

    async def exercise() -> None:
        semaphore = asyncio.Semaphore(2)
        await asyncio.gather(
            *(
                matrix_runner.run_profile(
                    profile,
                    launch_index=0,
                    launch_interval_seconds=0,
                    output_root=output_root,
                    log_root=log_root,
                    semaphore=semaphore,
                    resume=True,
                    retry_model_terminal=True,
                    retry_missing_snapshot_evidence=True,
                )
                for profile in profiles
            )
        )

    asyncio.run(exercise())

    assert maximum_active == 2
    assert all("--resume" in command for command in commands)
    assert all("--retry-model-terminal" in command for command in commands)
    assert all("--retry-missing-snapshot-evidence" in command for command in commands)
    assert all(command[command.index("--concurrency") + 1] == "40" for command in commands)
    assert all(command[command.index("--lifecycle-concurrency") + 1] == "3" for command in commands)
    assert all(command[command.index("--cleanup-concurrency") + 1] == "3" for command in commands)


def test_control_plane_retry_recovers_transient_cli_failures(monkeypatch: Any) -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise runner.ArgaCliError("transient staging failure")
        return "ready"

    monkeypatch.setattr(runner, "CONTROL_PLANE_RETRY_BASE_SECONDS", 0)

    assert asyncio.run(runner.retry_arga_cli(operation, label="test operation")) == "ready"
    assert attempts == 3


def test_matrix_serializes_google_profiles(tmp_path: Path, monkeypatch: Any) -> None:
    active = 0
    maximum_active = 0

    class FakeProcess:
        async def wait(self) -> int:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return 0

    async def fake_create_subprocess_exec(*_command: str, **_kwargs: object) -> FakeProcess:
        return FakeProcess()

    monkeypatch.setattr(matrix_runner.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    output_root = tmp_path / "matrix"
    log_root = output_root / "logs"
    log_root.mkdir(parents=True)
    (output_root / "profiles").mkdir()
    profiles = [
        {
            "id": f"gemini-{index}",
            "provider": "google",
            "model_id": "gemini-test",
            "requested_effort": "default",
            "api_effort": "default",
        }
        for index in range(2)
    ]

    async def exercise() -> None:
        semaphore = asyncio.Semaphore(2)
        google_profile_semaphore = asyncio.Semaphore(1)
        await asyncio.gather(
            *(
                matrix_runner.run_profile(
                    profile,
                    launch_index=0,
                    launch_interval_seconds=0,
                    output_root=output_root,
                    log_root=log_root,
                    semaphore=semaphore,
                    google_profile_semaphore=google_profile_semaphore,
                    resume=True,
                )
                for profile in profiles
            )
        )

    asyncio.run(exercise())

    assert maximum_active == 1
