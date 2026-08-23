#!/usr/bin/env python3
"""Run independent ArgaBench repeats with shared provider limits."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import run_argabench_model_matrix as matrix

from arga_twins_benchmark.lifecycle import write_private_json


def repeat_numbers(start: int, count: int) -> tuple[int, ...]:
    if start < 1:
        raise ValueError("--repeat-start must be positive")
    if count < 1:
        raise ValueError("--repeat-count must be positive")
    return tuple(range(start, start + count))


def build_job_plan(profiles: Sequence[dict[str, Any]], repeats: Sequence[int]) -> list[tuple[int, dict[str, Any]]]:
    """Interleave repeats so neither repeat systematically receives earlier capacity."""

    return [(repeat, profile) for profile in matrix.provider_round_robin(list(profiles)) for repeat in repeats]


def matrix_config(
    *,
    profiles: Sequence[dict[str, Any]],
    repeat: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    task_ids = args.tasks or []
    task_count = len(task_ids) if task_ids else 40
    return {
        "protocol": "argabench-model-matrix-run/1",
        "suite_id": "argabench-40-v1",
        "profiles": list(profiles),
        "profile_count": len(profiles),
        "task_ids": task_ids,
        "scenarios_per_profile": task_count,
        "total_trials": len(profiles) * task_count,
        "global_trial_concurrency": args.profile_concurrency,
        "per_profile_trial_concurrency": args.tasks_per_profile,
        "per_profile_lifecycle_concurrency": args.lifecycle_concurrency,
        "per_profile_cleanup_concurrency": args.cleanup_concurrency,
        "profile_launch_interval_seconds": args.launch_interval_seconds,
        "attempts_per_model_scenario_pair": 1,
        "benchmark_repeat": repeat,
        "google_profile_concurrency_across_repeats": 1,
        "google_task_concurrency": 1,
        "environment": matrix.os.environ.get("ARGA_API_URL", "https://api.argalabs.com"),
        "started_at": matrix.utc_now(),
    }


def validate_existing_config(
    path: Path,
    *,
    profiles: Sequence[dict[str, Any]],
    repeat: int,
    task_ids: Sequence[str] = (),
) -> None:
    if not path.is_file():
        raise ValueError(f"cannot resume repeat {repeat}: matrix config is missing: {path}")
    payload: object = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"cannot resume repeat {repeat}: matrix config must be an object")
    typed_payload = cast(dict[str, object], payload)
    if typed_payload.get("suite_id") != "argabench-40-v1":
        raise ValueError(f"cannot resume repeat {repeat}: suite identity changed")
    if typed_payload.get("benchmark_repeat") != repeat:
        raise ValueError(f"cannot resume repeat {repeat}: repeat identity changed")
    configured = typed_payload.get("profiles")
    if not isinstance(configured, list):
        raise ValueError(f"cannot resume repeat {repeat}: matrix profiles are missing")
    typed_configured = cast(list[object], configured)
    configured_ids = [
        str(cast(dict[str, object], item).get("id")) for item in typed_configured if isinstance(item, dict)
    ]
    expected_ids = [str(item["id"]) for item in profiles]
    if configured_ids != expected_ids:
        raise ValueError(f"cannot resume repeat {repeat}: profile identities or order changed")
    if typed_payload.get("task_ids", []) != list(task_ids):
        raise ValueError(f"cannot resume repeat {repeat}: selected task identities changed")


def prepare_repeat_root(
    parent: Path,
    *,
    profiles: Sequence[dict[str, Any]],
    repeat: int,
    args: argparse.Namespace,
) -> Path:
    root = parent / f"repeat-{repeat}"
    exists = root.exists()
    if exists and not args.resume:
        raise FileExistsError(root)
    if exists:
        validate_existing_config(
            root / "matrix-config.json",
            profiles=profiles,
            repeat=repeat,
            task_ids=args.tasks or [],
        )
    else:
        root.mkdir(parents=True, exist_ok=False)
        write_private_json(
            root / "matrix-config.json",
            matrix_config(profiles=profiles, repeat=repeat, args=args),
        )
    (root / "logs").mkdir(mode=0o700, exist_ok=exists)
    (root / "profiles").mkdir(mode=0o700, exist_ok=exists)
    return root


def summarize_repeat(
    root: Path,
    *,
    repeat: int,
    profiles: Sequence[dict[str, Any]],
    outcomes: Sequence[dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    task_count = len(args.tasks) if args.tasks else 40
    summaries = [item["summary"] for item in outcomes if isinstance(item.get("summary"), dict)]
    summary = {
        "protocol": "argabench-model-matrix-summary/1",
        "suite_id": "argabench-40-v1",
        "benchmark_repeat": repeat,
        "profile_count": len(profiles),
        "profiles_with_summaries": len(summaries),
        "profiles_returned_zero": sum(item["returncode"] == 0 for item in outcomes),
        "expected_trials": len(profiles) * task_count,
        "recorded_trials": sum(int(item.get("attempts", 0) or 0) for item in summaries),
        "global_trial_concurrency": args.profile_concurrency,
        "resumed": args.resume,
        "retry_infrastructure_invalid": args.retry_infrastructure_invalid,
        "retry_model_terminal": args.retry_model_terminal,
        "retry_missing_snapshot_evidence": args.retry_missing_snapshot_evidence,
        "estimated_cost_usd": round(
            sum(float(item.get("estimated_cost_usd", 0.0) or 0.0) for item in summaries),
            8,
        ),
        "input_tokens": sum(int(item.get("input_tokens", 0) or 0) for item in summaries),
        "output_tokens": sum(int(item.get("output_tokens", 0) or 0) for item in summaries),
        "tool_calls": sum(int(item.get("tool_calls", 0) or 0) for item in summaries),
        "outcomes": list(outcomes),
        "finished_at": matrix.utc_now(),
    }
    write_private_json(root / "matrix-summary.json", summary)
    return summary


async def async_main(args: argparse.Namespace) -> int:
    if args.retry_infrastructure_invalid and not args.resume:
        raise ValueError("--retry-infrastructure-invalid requires --resume")
    if args.retry_model_terminal and not args.resume:
        raise ValueError("--retry-model-terminal requires --resume")
    if args.retry_missing_snapshot_evidence and not args.resume:
        raise ValueError("--retry-missing-snapshot-evidence requires --resume")

    repeats = repeat_numbers(args.repeat_start, args.repeat_count)
    profiles = matrix.provider_round_robin(matrix.load_profiles())
    task_count = len(args.tasks) if args.tasks else 40
    output = cast(Path, args.output).resolve()
    if output.exists() and not args.resume:
        raise FileExistsError(output)
    output.mkdir(parents=True, exist_ok=args.resume)
    roots = {
        repeat: prepare_repeat_root(
            output,
            profiles=profiles,
            repeat=repeat,
            args=args,
        )
        for repeat in repeats
    }

    parent_config = {
        "protocol": "argabench-model-repeats/1",
        "suite_id": "argabench-40-v1",
        "repeat_start": repeats[0],
        "repeat_count": len(repeats),
        "repeats": list(repeats),
        "profile_count": len(profiles),
        "task_ids": args.tasks or [],
        "trials_per_repeat": len(profiles) * task_count,
        "total_trials": len(repeats) * len(profiles) * task_count,
        "profile_concurrency_across_repeats": args.profile_concurrency,
        "tasks_per_profile": args.tasks_per_profile,
        "google_profile_concurrency_across_repeats": 1,
        "google_task_concurrency": 1,
        "started_at": matrix.utc_now(),
    }
    config_path = output / "repeat-config.json"
    if config_path.exists():
        existing: object = json.loads(config_path.read_text())
        if not isinstance(existing, dict):
            raise ValueError("cannot resume: repeat config must be an object")
        typed_existing = cast(dict[str, object], existing)
        for key in (
            "suite_id",
            "repeat_start",
            "repeat_count",
            "repeats",
            "profile_count",
            "task_ids",
        ):
            if typed_existing.get(key) != parent_config[key]:
                raise ValueError(f"cannot resume: repeat config changed: {key}")
    else:
        write_private_json(config_path, parent_config)

    profile_semaphore = asyncio.Semaphore(args.profile_concurrency)
    google_profile_semaphore = asyncio.Semaphore(1)
    plan = build_job_plan(profiles, repeats)

    async def run_one(index: int, repeat: int, profile: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        outcome = await matrix.run_profile(
            profile,
            launch_index=index,
            launch_interval_seconds=args.launch_interval_seconds,
            output_root=roots[repeat],
            log_root=roots[repeat] / "logs",
            semaphore=profile_semaphore,
            google_profile_semaphore=google_profile_semaphore,
            resume=args.resume,
            retry_infrastructure_invalid=args.retry_infrastructure_invalid,
            retry_model_terminal=args.retry_model_terminal,
            retry_missing_snapshot_evidence=args.retry_missing_snapshot_evidence,
            tasks_per_profile=args.tasks_per_profile,
            lifecycle_concurrency=args.lifecycle_concurrency,
            cleanup_concurrency=args.cleanup_concurrency,
            task_ids=args.tasks or [],
        )
        return repeat, outcome

    completed = await asyncio.gather(*(run_one(index, repeat, profile) for index, (repeat, profile) in enumerate(plan)))
    by_repeat: dict[int, list[dict[str, Any]]] = {repeat: [] for repeat in repeats}
    for repeat, outcome in completed:
        by_repeat[repeat].append(outcome)

    repeat_summaries = {
        repeat: summarize_repeat(
            roots[repeat],
            repeat=repeat,
            profiles=profiles,
            outcomes=by_repeat[repeat],
            args=args,
        )
        for repeat in repeats
    }
    summary = {
        "protocol": "argabench-model-repeats-summary/1",
        "suite_id": "argabench-40-v1",
        "repeats": list(repeats),
        "expected_trials": len(repeats) * len(profiles) * task_count,
        "recorded_trials": sum(item["recorded_trials"] for item in repeat_summaries.values()),
        "profiles_returned_zero": sum(item["profiles_returned_zero"] for item in repeat_summaries.values()),
        "expected_profile_runs": len(repeats) * len(profiles),
        "estimated_cost_usd": round(sum(item["estimated_cost_usd"] for item in repeat_summaries.values()), 8),
        "input_tokens": sum(item["input_tokens"] for item in repeat_summaries.values()),
        "output_tokens": sum(item["output_tokens"] for item in repeat_summaries.values()),
        "tool_calls": sum(item["tool_calls"] for item in repeat_summaries.values()),
        "repeat_summaries": repeat_summaries,
        "finished_at": matrix.utc_now(),
    }
    write_private_json(output / "repeat-summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
    return (
        0
        if summary["recorded_trials"] == summary["expected_trials"]
        and summary["profiles_returned_zero"] == summary["expected_profile_runs"]
        else 1
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--task",
        dest="tasks",
        action="append",
        help="Run only this task id; repeat to select multiple tasks.",
    )
    parser.add_argument("--repeat-start", type=int, default=2)
    parser.add_argument("--repeat-count", type=int, default=2)
    parser.add_argument("--profile-concurrency", type=int, choices=range(1, 31), default=10)
    parser.add_argument("--tasks-per-profile", type=int, choices=range(1, 41), default=4)
    parser.add_argument("--launch-interval-seconds", type=float, default=0.25)
    parser.add_argument("--lifecycle-concurrency", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--cleanup-concurrency", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-infrastructure-invalid", action="store_true")
    parser.add_argument("--retry-model-terminal", action="store_true")
    parser.add_argument("--retry-missing-snapshot-evidence", action="store_true")
    return parser.parse_args()


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
