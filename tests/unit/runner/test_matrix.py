from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from arga_twins_benchmark.runner.matrix import (
    _preliminary_grade,  # pyright: ignore[reportPrivateUsage]
    _prepare_trial_attempt,  # pyright: ignore[reportPrivateUsage]
    _trace_call_records,  # pyright: ignore[reportPrivateUsage]
    build_trial_plans,
    load_env_file,
    load_experiment_bundles,
)
from arga_twins_benchmark.runner.prompting import MODEL_PROFILES


def _write_confirmed_cleanup(trial_dir: Path, *, run_id: str = "run-1") -> None:
    (trial_dir / "control.json").write_text(json.dumps({"scenario_id": "scenario-1", "run_id": run_id}))
    (trial_dir / "cleanup.json").write_text(
        json.dumps(
            {
                "twin_run": {
                    "run_id": run_id,
                    "status": "cancelled",
                    "twins": {},
                }
            }
        )
    )


def test_load_experiment_bundles_resolves_prompts_bindings_and_verifiers() -> None:
    experiment, bundles = load_experiment_bundles(Path("benchmark"), "development_pilot_48_v1")

    assert len(experiment.instances) == 48
    assert len(bundles) == 48
    bundle = bundles["blocking_code_review_v1_github_clean_001"]
    assert bundle.binding.roles == {"code_host": "github"}
    assert bundle.prompt.startswith("Act as the security reviewer")
    assert bundle.verification.output_contract.required_facts["decision"] == "changes_requested"


def test_build_trial_plans_interleaves_models_per_instance() -> None:
    experiment, _ = load_experiment_bundles(Path("benchmark"), "development_pilot_48_v1")

    plans = build_trial_plans(
        experiment,
        suite_run_id="suite-1",
        model_profiles=MODEL_PROFILES,
        repeats=1,
    )

    assert len(plans) == 144
    assert [plan.model.model_id for plan in plans[:3]] == [profile.model_id for profile in MODEL_PROFILES]
    assert len({plan.trial_id for plan in plans}) == 144


def test_load_env_file_does_not_overwrite_existing_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING_VALUE=file\nNEW_VALUE=secret\n")
    monkeypatch.setenv("EXISTING_VALUE", "process")
    monkeypatch.delenv("NEW_VALUE", raising=False)

    loaded = load_env_file(env_path)

    assert loaded == ["NEW_VALUE"]
    assert os.environ["EXISTING_VALUE"] == "process"
    assert os.environ["NEW_VALUE"] == "secret"


def test_resume_skips_completed_trial_and_records_original_runner_commit(tmp_path: Path) -> None:
    trial_id = "completed-trial"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    (tmp_path / "suite.json").write_text(json.dumps({"runner_commit": "original-commit"}))
    completed = {
        "terminal": True,
        "status": "completed",
        "trial_id": trial_id,
        "cleanup_succeeded": True,
    }
    (trial_dir / "result.json").write_text(json.dumps(completed))
    _write_confirmed_cleanup(trial_dir)

    prepared_dir, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="resume-commit",
        )
    )

    assert prepared_dir == trial_dir
    assert attempt == 1
    assert existing == completed
    assert not (tmp_path / "attempts").exists()
    metadata = json.loads((trial_dir / "attempt.json").read_text())
    assert metadata["runner_commit"] == "original-commit"
    assert metadata["terminal_result_preserved"] is True


@pytest.mark.parametrize(
    "capture_error",
    [
        "google_drive admin state returned HTTP 504",
        "gmail admin state returned HTTP 410",
        "github admin state returned HTTP 410",
    ],
)
def test_resume_archives_retryable_state_capture_error_and_returns_fresh_attempt_directory(
    tmp_path: Path,
    capture_error: str,
) -> None:
    trial_id = "failed-trial"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "terminal": True,
                "status": "runtime_error",
                "error_type": "_StateCaptureHttpError",
                "error": capture_error,
                "cleanup_succeeded": True,
                "runner_commit": "failed-commit",
            }
        )
    )
    _write_confirmed_cleanup(trial_dir)

    prepared_dir, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="fixed-commit",
        )
    )

    archive = tmp_path / "attempts" / trial_id / "attempt-0001"
    assert existing is None
    assert attempt == 2
    assert prepared_dir == trial_dir
    assert list(prepared_dir.iterdir()) == []
    assert json.loads((archive / "result.json").read_text())["status"] == "runtime_error"
    metadata = json.loads((archive / "attempt.json").read_text())
    assert metadata["runner_commit"] == "failed-commit"
    assert metadata["archive_reason"] == "runtime_error"


def test_resume_preserves_arbitrary_programming_runtime_error_as_terminal(tmp_path: Path) -> None:
    trial_id = "programming-error"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    programming_error = {
        "terminal": True,
        "status": "runtime_error",
        "error_type": "RuntimeError",
        "error": "unexpected application invariant",
        "cleanup_succeeded": True,
    }
    (trial_dir / "result.json").write_text(json.dumps(programming_error))
    _write_confirmed_cleanup(trial_dir)

    prepared_dir, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="fixed-commit",
        )
    )

    assert prepared_dir == trial_dir
    assert attempt == 1
    assert existing == programming_error
    assert not (tmp_path / "attempts").exists()


def test_resume_blocks_tls_transport_retry_without_cleanup_identity(tmp_path: Path) -> None:
    trial_id = "tls-transport-error"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "terminal": True,
                "status": "runtime_error",
                "error_type": "SSLError",
                "error": "ssl/tls alert bad record mac",
                "cleanup_succeeded": True,
                "started_at": "2099-01-01T00:00:00+00:00",
            }
        )
    )

    _, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="fixed-commit",
        )
    )

    assert existing is not None
    assert existing["resume_blocked"] is True
    assert existing["error_type"] == "OrphanTwinLeaseActive"
    assert attempt == 1
    assert not (tmp_path / "attempts").exists()


def test_resume_confirms_historical_cleaning_up_artifact_before_fresh_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trial_id = "historical-cleaning-up"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    control_path = trial_dir / "control.json"
    control_path.write_text('{"scenario_id": "scenario-1", "run_id": "run-1"}')
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "terminal": True,
                "status": "runtime_error",
                "error_type": "SSLError",
                "error": "ssl/tls alert bad record mac",
                "cleanup_succeeded": True,
            }
        )
    )
    _write_confirmed_cleanup(trial_dir)
    (trial_dir / "cleanup.json").write_text('{"twin_run":{"run_id":"run-1","status":"cleaning_up","twins":{}}}')
    cleaned: list[Path] = []

    async def fake_cleanup(path: Path) -> dict[str, Any]:
        cleaned.append(path)
        return {
            "twin_run": {
                "run_id": "run-1",
                "status": "cancelled",
                "twins": {},
            }
        }

    monkeypatch.setattr("arga_twins_benchmark.runner.matrix.cleanup_instance", fake_cleanup)

    _, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="fixed-commit",
        )
    )

    assert cleaned == [control_path]
    assert existing is None
    assert attempt == 2
    archived = tmp_path / "attempts" / trial_id / "attempt-0001"
    assert json.loads((archived / "resume-cleanup.json").read_text())["twin_run"]["status"] == "cancelled"


def test_resume_archives_transient_model_api_500_for_fresh_twin_retry(tmp_path: Path) -> None:
    trial_id = "model-api-500"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "terminal": True,
                "status": "api_error",
                "stop_reason": "http_500",
                "cleanup_succeeded": True,
                "tool_calls": 0,
            }
        )
    )
    _write_confirmed_cleanup(trial_dir)

    _, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="fixed-commit",
        )
    )

    assert existing is None
    assert attempt == 2
    archived = tmp_path / "attempts" / trial_id / "attempt-0001"
    assert json.loads((archived / "result.json").read_text())["status"] == "api_error"
    assert json.loads((archived / "attempt.json").read_text())["archive_reason"] == "api_error"


def test_resume_archives_model_transport_error_for_fresh_twin_retry(tmp_path: Path) -> None:
    trial_id = "model-transport-error"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "terminal": True,
                "status": "api_error",
                "stop_reason": "transport_error",
                "cleanup_succeeded": True,
                "tool_calls": 0,
            }
        )
    )
    _write_confirmed_cleanup(trial_dir)

    _, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="fixed-commit",
        )
    )

    assert existing is None
    assert attempt == 2
    archived = tmp_path / "attempts" / trial_id / "attempt-0001"
    assert json.loads((archived / "result.json").read_text())["stop_reason"] == ("transport_error")


def test_resume_rejects_cleanup_evidence_for_a_different_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trial_id = "mismatched-cleanup-run"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "terminal": True,
                "status": "runtime_error",
                "error_type": "SSLError",
                "error": "transient",
                "cleanup_succeeded": True,
            }
        )
    )
    _write_confirmed_cleanup(trial_dir, run_id="run-expected")
    (trial_dir / "cleanup.json").write_text('{"twin_run":{"run_id":"run-other","status":"cancelled","twins":{}}}')

    async def mismatched_cleanup(_: Path) -> dict[str, Any]:
        return {
            "twin_run": {
                "run_id": "run-other",
                "status": "cancelled",
                "twins": {},
            }
        }

    monkeypatch.setattr(
        "arga_twins_benchmark.runner.matrix.cleanup_instance",
        mismatched_cleanup,
    )

    _, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="fixed-commit",
        )
    )

    assert attempt == 1
    assert existing is not None
    assert existing["resume_blocked"] is True
    assert existing["cleanup_succeeded"] is False
    assert not (tmp_path / "attempts").exists()


def test_resume_preserves_non_transient_model_api_error(tmp_path: Path) -> None:
    trial_id = "model-api-400"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    api_error = {
        "terminal": True,
        "status": "api_error",
        "stop_reason": "http_400",
        "cleanup_succeeded": True,
    }
    (trial_dir / "result.json").write_text(json.dumps(api_error))
    _write_confirmed_cleanup(trial_dir)

    _, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="fixed-commit",
        )
    )

    assert existing == api_error
    assert attempt == 1
    assert not (tmp_path / "attempts").exists()


def test_resume_retries_cleanup_without_replaying_completed_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trial_id = "completed-cleanup-failure"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    control_path = trial_dir / "control.json"
    control_path.write_text('{"scenario_id": "scenario-1", "run_id": "run-1"}')
    completed = {
        "terminal": True,
        "status": "completed",
        "trial_id": trial_id,
        "attempt": 1,
        "final_text": "preserve this exact answer",
        "cleanup_succeeded": False,
    }
    (trial_dir / "result.json").write_text(json.dumps(completed))
    cleaned: list[Path] = []

    async def fake_cleanup(path: Path) -> dict[str, Any]:
        cleaned.append(path)
        return {
            "twin_run": {
                "run_id": "run-1",
                "status": "cancelled",
                "twins": {},
            }
        }

    monkeypatch.setattr("arga_twins_benchmark.runner.matrix.cleanup_instance", fake_cleanup)

    prepared_dir, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="resume-commit",
        )
    )

    assert cleaned == [control_path]
    assert prepared_dir == trial_dir
    assert attempt == 1
    assert existing is not None
    assert existing["status"] == "completed"
    assert existing["final_text"] == "preserve this exact answer"
    assert existing["cleanup_succeeded"] is True
    assert not (tmp_path / "attempts").exists()
    persisted = json.loads((trial_dir / "result.json").read_text())
    assert persisted["cleanup_succeeded"] is True
    assert persisted["cleanup"]["twin_run"]["status"] == "cancelled"


def test_resume_preserves_completed_model_when_cleanup_retry_still_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trial_id = "completed-cleanup-still-failing"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    (trial_dir / "control.json").write_text('{"scenario_id": "scenario-1", "run_id": "run-1"}')
    completed = {
        "terminal": True,
        "status": "completed",
        "trial_id": trial_id,
        "attempt": 1,
        "final_text": "do not replay me",
        "cleanup_succeeded": False,
    }
    (trial_dir / "result.json").write_text(json.dumps(completed))

    async def fake_cleanup(_: Path) -> dict[str, Any]:
        raise RuntimeError("CLI teardown still unavailable")

    monkeypatch.setattr("arga_twins_benchmark.runner.matrix.cleanup_instance", fake_cleanup)

    _, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="resume-commit",
        )
    )

    assert attempt == 1
    assert existing is not None
    assert existing["status"] == "completed"
    assert existing["final_text"] == "do not replay me"
    assert existing["cleanup_succeeded"] is False
    assert existing["cleanup"]["error_type"] == "RuntimeError"
    assert not (tmp_path / "attempts").exists()


def test_resume_reconciles_successful_cleanup_artifact_without_replaying_or_teardown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trial_id = "completed-cleanup-result-write-crash"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    (trial_dir / "control.json").write_text('{"scenario_id": "scenario-1", "run_id": "run-1"}')
    (trial_dir / "cleanup.json").write_text('{"twin_run":{"run_id":"run-1","status":"cancelled","twins":{}}}')
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "terminal": True,
                "status": "completed",
                "trial_id": trial_id,
                "attempt": 1,
                "final_text": "already completed",
                "cleanup_succeeded": False,
            }
        )
    )

    async def unexpected_cleanup(_: Path) -> dict[str, Any]:
        raise AssertionError("proven cleanup must not be repeated")

    monkeypatch.setattr(
        "arga_twins_benchmark.runner.matrix.cleanup_instance",
        unexpected_cleanup,
    )

    _, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="resume-commit",
        )
    )

    assert attempt == 1
    assert existing is not None
    assert existing["status"] == "completed"
    assert existing["final_text"] == "already completed"
    assert existing["cleanup_succeeded"] is True
    assert existing["cleanup"]["twin_run"]["status"] == "cancelled"
    assert not (tmp_path / "attempts").exists()


def test_resume_archives_cancelled_runner_attempt_after_orphan_lease_expiry(
    tmp_path: Path,
) -> None:
    trial_id = "cancelled-trial"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "terminal": True,
                "status": "runtime_error",
                "error_type": "CancelledError",
                "error": "",
                "cleanup_succeeded": True,
                "started_at": "2020-01-01T00:00:00+00:00",
            }
        )
    )

    _, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="fixed-commit",
        )
    )

    assert existing is None
    assert attempt == 2
    archived = tmp_path / "attempts" / trial_id / "attempt-0001"
    assert json.loads((archived / "result.json").read_text())["error_type"] == "CancelledError"
    expiry = json.loads((archived / "resume-cleanup.json").read_text())
    assert expiry["protocol"] == "arga-bench-orphan-lease-expiry/1"
    assert expiry["outcome"] == "lease_window_elapsed"


def test_resume_confirms_legacy_failed_provision_run_before_fresh_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trial_id = "legacy-failed-provision"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    run_id = "6b2d9f94-0d4e-4fc4-be53-736aff750f26"
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "terminal": True,
                "status": "runtime_error",
                "error_type": "ArgaCliError",
                "error": (f"twin run {run_id} ended in status 'failed': Timed out waiting for job job-1"),
                "cleanup_succeeded": False,
            }
        )
    )
    cleaned: list[str] = []

    async def fake_cleanup(target_run_id: str) -> dict[str, Any]:
        cleaned.append(target_run_id)
        return {"twin_run": {"run_id": target_run_id, "status": "failed", "twins": {}}}

    monkeypatch.setattr("arga_twins_benchmark.runner.matrix.cleanup_twin_run", fake_cleanup)

    _, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="fixed-commit",
        )
    )

    assert cleaned == [run_id]
    assert existing is None
    assert attempt == 2
    archived = tmp_path / "attempts" / trial_id / "attempt-0001"
    assert json.loads((archived / "resume-cleanup.json").read_text())["twin_run"]["run_id"] == run_id


def test_resume_cleans_interrupted_twin_before_archiving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trial_id = "interrupted-trial"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    control_path = trial_dir / "control.json"
    control_path.write_text('{"run_id": "old-run"}')
    (trial_dir / "state.json").write_text('{"phase": "invocation_started"}')
    cleaned: list[Path] = []

    async def fake_cleanup(path: Path) -> dict[str, Any]:
        cleaned.append(path)
        return {
            "twin_run": {
                "run_id": "old-run",
                "status": "cancelled",
                "twins": {},
            }
        }

    monkeypatch.setattr("arga_twins_benchmark.runner.matrix.cleanup_instance", fake_cleanup)

    prepared_dir, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="fixed-commit",
        )
    )

    archive = tmp_path / "attempts" / trial_id / "attempt-0001"
    assert cleaned == [control_path]
    assert existing is None
    assert attempt == 2
    assert prepared_dir.is_dir()
    assert json.loads((archive / "resume-cleanup.json").read_text())["twin_run"]["status"] == "cancelled"
    assert json.loads((archive / "attempt.json").read_text())["archive_reason"] == ("interrupted_nonterminal_attempt")


def test_resume_blocks_fresh_provision_when_old_twin_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trial_id = "unsafe-trial"
    trial_dir = tmp_path / "trials" / trial_id
    trial_dir.mkdir(parents=True)
    (trial_dir / "control.json").write_text('{"run_id": "old-run"}')

    async def fake_cleanup(_: Path) -> dict[str, Any]:
        raise RuntimeError("CLI teardown unavailable")

    monkeypatch.setattr("arga_twins_benchmark.runner.matrix.cleanup_instance", fake_cleanup)

    prepared_dir, attempt, blocked = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id=trial_id,
            runner_commit="fixed-commit",
        )
    )

    assert prepared_dir == trial_dir
    assert attempt == 1
    assert blocked is not None
    assert blocked["resume_blocked"] is True
    assert blocked["cleanup_succeeded"] is False
    assert not (tmp_path / "attempts").exists()


def test_approved_share_read_only_post_calls_are_not_marked_as_mutations() -> None:
    _, bundles = load_experiment_bundles(Path("benchmark"), "development_pilot_48_v1")
    bundle = bundles["approved_external_share_v1_drive_notion_authorized_001"]
    raw_calls = [
        ("knowledge_base", "notion", "POST", "/v1/search", None),
        (
            "knowledge_base",
            "notion",
            "GET",
            "/v1/pages/bb1cddba-338e-5325-45f9-99add99c1ce3/markdown",
            None,
        ),
        (
            "knowledge_base",
            "notion",
            "POST",
            "/v1/data_sources/fe55d721-b79e-8895-697a-ef3f1076ada0/query",
            None,
        ),
        (
            "knowledge_base",
            "notion",
            "POST",
            "/v1/data_sources/7ae509a3-c74b-6732-54ca-94d081deaacc/query",
            None,
        ),
        ("storage", "google_drive", "GET", "/drive/v3/files", None),
        (
            "storage",
            "google_drive",
            "GET",
            "/drive/v3/files/RbFzM19Zhv78TH0WlylTNT99q8I?alt=media",
            None,
        ),
        (
            "storage",
            "google_drive",
            "GET",
            "/drive/v3/files/NCCbvVIpFy5EYlDQXSFuK-NzVjU?alt=media",
            None,
        ),
        (
            "storage",
            "google_drive",
            "GET",
            "/drive/v3/files/RbFzM19Zhv78TH0WlylTNT99q8I/permissions",
            None,
        ),
        (
            "storage",
            "google_drive",
            "POST",
            "/drive/v3/files/RbFzM19Zhv78TH0WlylTNT99q8I/permissions",
            None,
        ),
        (
            "storage",
            "google_drive",
            "GET",
            "/drive/v3/files/RbFzM19Zhv78TH0WlylTNT99q8I/permissions/1",
            None,
        ),
    ]
    gateway = cast(
        Any,
        SimpleNamespace(
            trace_records=tuple(
                SimpleNamespace(
                    requested_provider=role,
                    provider=provider,
                    method=method,
                    path=path,
                    status_code=200,
                    operation=None,
                    operation_type=operation_type,
                )
                for role, provider, method, path, operation_type in raw_calls
            )
        ),
    )

    records = _trace_call_records(gateway, bundle)
    preliminary = _preliminary_grade(
        bundle,
        gateway=gateway,
        output=('{"request":"SHARE-311","decision":"shared","recipient":"auditor@trusted.example","role":"reader"}'),
    )

    assert [record.mutating for record in records] == [False] * 8 + [True, False]
    full_grade = preliminary["full_deterministic_grade"]
    assert full_grade["assertion_results"]["trace.allowed_mutations"] is True
    assert "unauthorized_provider_call" not in full_grade["harm_vector"]
