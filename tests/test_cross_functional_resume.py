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

    runner._write_resume_history(tmp_path, concurrency=7, decisions=decisions, plans=plans)

    history = json.loads((tmp_path / "resume-history.json").read_text())
    assert history["entries"][0]["concurrency"] == 7
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
                )
                for profile in profiles
            )
        )

    asyncio.run(exercise())

    assert maximum_active == 2
    assert all("--resume" in command for command in commands)
    assert all(command[command.index("--concurrency") + 1] == "40" for command in commands)
    assert all(command[command.index("--lifecycle-concurrency") + 1] == "3" for command in commands)
    assert all(command[command.index("--cleanup-concurrency") + 1] == "3" for command in commands)
