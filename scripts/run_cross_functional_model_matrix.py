#!/usr/bin/env python3
"""Run every Cross-Functional 40 model profile with a global trial cap."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from arga_twins_benchmark.lifecycle import write_private_json

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "benchmark" / "cross_functional_40" / "model_matrix.json"
RUNNER_PATH = ROOT / "scripts" / "run_cross_functional_40.py"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def load_profiles() -> list[dict[str, Any]]:
    payload = json.loads(MATRIX_PATH.read_text())
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, list):
        raise ValueError("model matrix profiles must be an array")
    profiles = [cast(dict[str, Any], item) for item in raw_profiles if isinstance(item, dict)]
    if len(profiles) != 30 or len({str(item.get("id")) for item in profiles}) != 30:
        raise ValueError("model matrix must contain exactly 30 unique profiles")
    return profiles


def provider_round_robin(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_provider: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for profile in profiles:
        by_provider[str(profile["provider"])].append(profile)
    ordered: list[dict[str, Any]] = []
    provider_order = ("anthropic", "openai", "google")
    while any(by_provider.values()):
        for provider in provider_order:
            if by_provider[provider]:
                ordered.append(by_provider[provider].popleft())
    return ordered


async def run_profile(
    profile: dict[str, Any],
    *,
    launch_index: int,
    launch_interval_seconds: float,
    output_root: Path,
    log_root: Path,
    semaphore: asyncio.Semaphore,
    resume: bool,
    retry_infrastructure_invalid: bool = False,
    tasks_per_profile: int = 40,
    lifecycle_concurrency: int = 3,
    cleanup_concurrency: int = 3,
) -> dict[str, Any]:
    profile_id = str(profile["id"])
    output = output_root / "profiles" / profile_id
    log_path = log_root / f"{profile_id}.log"
    effective_task_concurrency = 1 if profile["provider"] == "google" else tasks_per_profile
    async with semaphore:
        await asyncio.sleep(launch_index * launch_interval_seconds)
        started_at = utc_now()
        command = [
            sys.executable,
            str(RUNNER_PATH),
            "--profile",
            profile_id,
            "--output",
            str(output),
            "--concurrency",
            str(effective_task_concurrency),
            "--lifecycle-concurrency",
            str(lifecycle_concurrency),
            "--cleanup-concurrency",
            str(cleanup_concurrency),
        ]
        if resume:
            command.append("--resume")
        if retry_infrastructure_invalid:
            command.append("--retry-infrastructure-invalid")
        with log_path.open("ab" if resume else "wb") as log_file:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(ROOT),
                env=os.environ.copy(),
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                returncode = await process.wait()
            except asyncio.CancelledError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except TimeoutError:
                    process.kill()
                    await process.wait()
                raise
        summary_path = output / "run-summary.json"
        summary: object = None
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text())
        outcome = {
            "profile_id": profile_id,
            "provider": profile["provider"],
            "model_id": profile["model_id"],
            "requested_effort": profile["requested_effort"],
            "api_effort": profile["api_effort"],
            "task_concurrency": effective_task_concurrency,
            "resumed": resume,
            "retry_infrastructure_invalid": retry_infrastructure_invalid,
            "started_at": started_at,
            "finished_at": utc_now(),
            "returncode": returncode,
            "summary": summary,
            "log": str(log_path),
        }
        print(json.dumps(outcome, sort_keys=True), flush=True)
        return outcome


async def async_main(args: argparse.Namespace) -> int:
    if args.retry_infrastructure_invalid and not args.resume:
        raise ValueError("--retry-infrastructure-invalid requires --resume")
    profiles = provider_round_robin(load_profiles())
    output_root = args.output.resolve()
    resume_existing = args.resume and output_root.exists()
    if resume_existing:
        config_path = output_root / "matrix-config.json"
        if not config_path.is_file():
            raise ValueError(f"cannot resume: matrix config is missing: {config_path}")
        config = json.loads(config_path.read_text())
        if not isinstance(config, dict):
            raise ValueError("cannot resume: matrix config must be an object")
        if config.get("suite_id") != "cross-functional-40-v1":
            raise ValueError("cannot resume: matrix suite identity changed")
        configured_profiles = config.get("profiles")
        if not isinstance(configured_profiles, list):
            raise ValueError("cannot resume: matrix profiles are missing")
        configured_ids = [str(item.get("id")) for item in configured_profiles if isinstance(item, dict)]
        if configured_ids != [str(item["id"]) for item in profiles]:
            raise ValueError("cannot resume: matrix profile identities or order changed")
    else:
        output_root.mkdir(parents=True, exist_ok=False)
    log_root = output_root / "logs"
    log_root.mkdir(mode=0o700, exist_ok=resume_existing)
    (output_root / "profiles").mkdir(mode=0o700, exist_ok=resume_existing)
    if not resume_existing:
        write_private_json(
            output_root / "matrix-config.json",
            {
                "protocol": "arga-bench-cross-functional-model-matrix-run/1",
                "suite_id": "cross-functional-40-v1",
                "profiles": profiles,
                "profile_count": len(profiles),
                "scenarios_per_profile": 40,
                "total_trials": len(profiles) * 40,
                "global_trial_concurrency": args.concurrency,
                "per_profile_trial_concurrency": args.tasks_per_profile,
                "per_profile_lifecycle_concurrency": args.lifecycle_concurrency,
                "per_profile_cleanup_concurrency": args.cleanup_concurrency,
                "profile_launch_interval_seconds": args.launch_interval_seconds,
                "attempts_per_model_scenario_pair": 1,
                "environment": os.environ.get("ARGA_API_URL", "https://api.argalabs.com"),
                "started_at": utc_now(),
            },
        )
    semaphore = asyncio.Semaphore(args.concurrency)
    outcomes = await asyncio.gather(
        *(
            run_profile(
                profile,
                launch_index=launch_index,
                launch_interval_seconds=args.launch_interval_seconds,
                output_root=output_root,
                log_root=log_root,
                semaphore=semaphore,
                resume=args.resume,
                retry_infrastructure_invalid=args.retry_infrastructure_invalid,
                tasks_per_profile=args.tasks_per_profile,
                lifecycle_concurrency=args.lifecycle_concurrency,
                cleanup_concurrency=args.cleanup_concurrency,
            )
            for launch_index, profile in enumerate(profiles)
        )
    )
    summaries = [item["summary"] for item in outcomes if isinstance(item.get("summary"), dict)]
    matrix_summary = {
        "protocol": "arga-bench-cross-functional-model-matrix-summary/1",
        "suite_id": "cross-functional-40-v1",
        "profile_count": len(profiles),
        "profiles_with_summaries": len(summaries),
        "profiles_returned_zero": sum(item["returncode"] == 0 for item in outcomes),
        "expected_trials": len(profiles) * 40,
        "recorded_trials": sum(int(item.get("attempts", 0) or 0) for item in summaries),
        "global_trial_concurrency": args.concurrency,
        "resumed": resume_existing,
        "retry_infrastructure_invalid": args.retry_infrastructure_invalid,
        "estimated_cost_usd": round(
            sum(float(item.get("estimated_cost_usd", 0.0) or 0.0) for item in summaries),
            8,
        ),
        "input_tokens": sum(int(item.get("input_tokens", 0) or 0) for item in summaries),
        "output_tokens": sum(int(item.get("output_tokens", 0) or 0) for item in summaries),
        "tool_calls": sum(int(item.get("tool_calls", 0) or 0) for item in summaries),
        "outcomes": outcomes,
        "finished_at": utc_now(),
    }
    write_private_json(output_root / "matrix-summary.json", matrix_summary)
    print(json.dumps(matrix_summary, indent=2, sort_keys=True), flush=True)
    return (
        0
        if matrix_summary["recorded_trials"] == matrix_summary["expected_trials"]
        and matrix_summary["profiles_returned_zero"] == len(profiles)
        else 1
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, choices=range(1, 31), default=2)
    parser.add_argument("--launch-interval-seconds", type=float, default=0.5)
    parser.add_argument("--tasks-per-profile", type=int, choices=range(1, 41), default=40)
    parser.add_argument("--lifecycle-concurrency", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--cleanup-concurrency", type=int, choices=range(1, 11), default=3)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume the matrix and let each profile retry only proven zero-invocation failures.",
    )
    parser.add_argument(
        "--retry-infrastructure-invalid",
        action="store_true",
        help=(
            "With --resume, retry only attempts explicitly classified infrastructure-invalid; "
            "valid and model-terminal trials remain immutable."
        ),
    )
    return parser.parse_args()


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
