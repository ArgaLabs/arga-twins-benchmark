from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
SCRIPT_PATH = SCRIPT_DIR / "run_argabench_model_repeats.py"
SPEC = importlib.util.spec_from_file_location("argabench_repeat_runner", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def _args(output: Path, *, resume: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        output=output,
        tasks=None,
        repeat_start=2,
        repeat_count=2,
        profile_concurrency=10,
        tasks_per_profile=4,
        launch_interval_seconds=0.25,
        lifecycle_concurrency=3,
        cleanup_concurrency=3,
        resume=resume,
        retry_infrastructure_invalid=False,
        retry_model_terminal=False,
        retry_missing_snapshot_evidence=False,
    )


def test_repeat_plan_contains_two_independent_trials_for_all_profiles() -> None:
    profiles = runner.matrix.load_profiles()
    plan = runner.build_job_plan(profiles, runner.repeat_numbers(2, 2))

    assert len(plan) == 64
    assert {repeat for repeat, _profile in plan} == {2, 3}
    counts: dict[str, int] = {}
    for _repeat, profile in plan:
        profile_id = str(profile["id"])
        counts[profile_id] = counts.get(profile_id, 0) + 1
    assert set(counts.values()) == {2}
    assert sum(profile["provider"] == "google" for _repeat, profile in plan) == 6


def test_matrix_includes_gemini_3_7_flash_default_profile() -> None:
    profile = next(profile for profile in runner.matrix.load_profiles() if profile["id"] == "gemini-3-7-flash-default")

    assert profile == {
        "id": "gemini-3-7-flash-default",
        "label": "Gemini 3.7 Flash",
        "provider": "google",
        "model_id": "gemini-3.7-flash",
        "requested_effort": "default",
        "api_effort": "default",
        "thinking": "model_default",
        "input_usd_per_million": 0.75,
        "output_usd_per_million": 3.75,
        "cache_read_usd_per_million": 0.075,
        "pricing_source": "https://ai.google.dev/gemini-api/docs/pricing",
    }


def test_repeat_roots_record_identity_and_reject_changed_resume(tmp_path: Path) -> None:
    args = _args(tmp_path / "runs")
    profiles: list[dict[str, Any]] = runner.matrix.provider_round_robin(runner.matrix.load_profiles())
    args.output.mkdir()
    root = runner.prepare_repeat_root(
        args.output,
        profiles=profiles,
        repeat=2,
        args=args,
    )

    payload = runner.json.loads((root / "matrix-config.json").read_text())
    assert payload["benchmark_repeat"] == 2
    assert payload["total_trials"] == 1_280
    assert payload["google_profile_concurrency_across_repeats"] == 1

    resume_args = _args(args.output, resume=True)
    runner.prepare_repeat_root(
        args.output,
        profiles=profiles,
        repeat=2,
        args=resume_args,
    )

    payload["benchmark_repeat"] = 3
    (root / "matrix-config.json").write_text(runner.json.dumps(payload))
    try:
        runner.prepare_repeat_root(
            args.output,
            profiles=profiles,
            repeat=2,
            args=resume_args,
        )
    except ValueError as error:
        assert "repeat identity changed" in str(error)
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("changed repeat identity was accepted")


def test_repeat_config_counts_selected_tasks(tmp_path: Path) -> None:
    args = _args(tmp_path / "runs")
    args.tasks = ["it-03", "it-06"]
    profiles: list[dict[str, Any]] = runner.matrix.provider_round_robin(runner.matrix.load_profiles())

    payload = runner.matrix_config(profiles=profiles, repeat=1, args=args)

    assert payload["task_ids"] == ["it-03", "it-06"]
    assert payload["scenarios_per_profile"] == 2
    assert payload["total_trials"] == 64
