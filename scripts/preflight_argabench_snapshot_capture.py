#!/usr/bin/env python3
"""Validate ArgaBench trusted snapshots without invoking a model."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import traceback
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, cast

from arga_twins_benchmark.arga_cli import SubprocessArgaCli, TwinRun
from arga_twins_benchmark.evaluation.state_capture import (
    TrustedStateCapturer,
    diff_canonical_resources,
    diff_trusted_states,
)
from arga_twins_benchmark.lifecycle import cleanup_payload_proves_inert, write_private_json
from arga_twins_benchmark.reporting.argabench_fair import (
    canonicalize_argabench_snapshot,
    snapshot_capture_contract_gaps,
    snapshot_queries_for_task,
)

ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "benchmark" / "argabench_40" / "suite.json"
FAIR_GRADER_PATH = (
    ROOT / "src" / "arga_twins_benchmark" / "reporting" / "argabench_fair.py"
)
SUITE_TAG = "suite:argabench-40-v1"
PROVISION_TIMEOUT_SECONDS = 1_200
CAPTURE_TIMEOUT_SECONDS = 60
POLL_SECONDS = 2.0

PROVIDER_ROLES = {
    "github": "code_host",
    "gmail": "email",
    "google_calendar": "calendar",
    "google_drive": "file_storage",
    "hubspot": "hubspot_crm",
    "jira": "jira_tracker",
    "linear": "linear_tracker",
    "linkedin": "professional_network",
    "notion": "knowledge_base",
    "salesforce": "salesforce_crm",
    "slack": "team_chat",
    "stripe": "payments",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return jsonable(asdict(value))
    if isinstance(value, tuple | list):
        return [jsonable(item) for item in cast(Sequence[object], value)]
    if isinstance(value, dict):
        return {
            str(key): jsonable(item)
            for key, item in cast(Mapping[object, object], value).items()
        }
    return value


def content_hash(bundle: dict[str, Any]) -> str:
    payload = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def task_roles(task: dict[str, Any]) -> dict[str, str]:
    return {PROVIDER_ROLES[provider]: provider for provider in cast(list[str], task["twins"])}


def control_payload(task_id: str, scenario_id: str, digest: str, run: TwinRun) -> dict[str, Any]:
    return {
        "protocol": "arga-bench-control/1",
        "instance_id": task_id,
        "scenario_id": scenario_id,
        "scenario_content_sha256": digest,
        "run_id": run.run_id,
        "twin_run": dict(run.raw),
    }


def preflight_plan(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for task in tasks:
        queries = snapshot_queries_for_task(task)
        plan.append(
            {
                "task_id": task["id"],
                "domain": task["domain"],
                "twins": task["twins"],
                "query_count": len(queries),
                "query_ids": [query.id for query in queries],
            }
        )
    return plan


async def resolve_scenarios(tasks: list[dict[str, Any]]) -> dict[str, str]:
    async with SubprocessArgaCli() as arga:
        scenarios = await arga.list_scenarios(tag=SUITE_TAG)
    if len(scenarios) != 40:
        raise RuntimeError(f"expected 40 staging Scenarios tagged {SUITE_TAG!r}, found {len(scenarios)}")
    by_hash: dict[str, dict[str, Any]] = {}
    for scenario in scenarios:
        tags = scenario.get("tags")
        if not isinstance(tags, list):
            continue
        content_tags = [
            tag
            for tag in cast(list[object], tags)
            if isinstance(tag, str) and tag.startswith("content-sha256:")
        ]
        if len(content_tags) == 1:
            by_hash[content_tags[0].split(":", 1)[1]] = dict(scenario)
    resolved: dict[str, str] = {}
    for task in tasks:
        digest = content_hash(task)
        scenario = by_hash.get(digest)
        scenario_id = scenario.get("id") if scenario else None
        description = scenario.get("description") if scenario else None
        if not isinstance(scenario_id, str) or description != task["prompt"]:
            raise RuntimeError(f"{task['id']}: no exact staging Scenario for content hash {digest}")
        resolved[cast(str, task["id"])] = scenario_id
    return resolved


async def wait_ready(arga: SubprocessArgaCli, run: TwinRun, *, task_dir: Path, control: dict[str, Any]) -> TwinRun:
    deadline = asyncio.get_running_loop().time() + PROVISION_TIMEOUT_SECONDS
    latest = run
    terminal = {"cancelled", "canceled", "expired", "failed", "error", "torn_down", "terminated"}
    while latest.status != "ready" and latest.status.casefold() not in terminal:
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"timed out waiting for twin run {latest.run_id}; status={latest.status}")
        await asyncio.sleep(POLL_SECONDS)
        latest = await arga.status(latest.run_id)
        write_private_json(
            task_dir / "control.json",
            {**control, "twin_run": dict(latest.raw)},
        )
    if latest.status != "ready":
        raise RuntimeError(f"twin run {latest.run_id} ended in status {latest.status!r}")
    return latest


async def wait_cleanup(arga: SubprocessArgaCli, run_id: str) -> dict[str, Any]:
    try:
        teardown: object = dict(await arga.teardown(run_id))
    except Exception as error:  # noqa: BLE001 - cleanup failure is recorded and returned
        teardown = {"error_type": type(error).__name__, "error": str(error)}
    deadline = asyncio.get_running_loop().time() + 180
    terminal = {"cancelled", "canceled", "expired", "torn_down", "terminated", "deleted", "cleaned_up"}
    while True:
        status = await arga.status(run_id)
        if status.status.casefold() in terminal and not status.twins:
            return {
                "twin_run": dict(status.raw),
                "teardown": {"outcome": "accepted", "response": teardown},
                "confirmation": {
                    "outcome": "terminal_without_twins",
                    "confirmed_status": status.status,
                },
            }
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"timed out confirming cleanup for twin run {run_id}")
        await asyncio.sleep(POLL_SECONDS)


async def preflight_task(
    task: dict[str, Any],
    *,
    scenario_id: str,
    output_root: Path,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        task_id = cast(str, task["id"])
        task_dir = output_root / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=False)
        started_at = utc_now()
        started_clock = monotonic()
        run: TwinRun | None = None
        cleanup: dict[str, Any] = {}
        error: BaseException | None = None
        query_count = 0
        canonical_resource_count = 0
        raw_delta_count = 0
        semantic_mutation_count = 0
        arga = SubprocessArgaCli()
        try:
            digest = content_hash(task)
            run = await arga.create_twin_run(
                twins=cast(list[str], task["twins"]),
                scenario_id=scenario_id,
                ttl_minutes=60,
            )
            control = control_payload(task_id, scenario_id, digest, run)
            write_private_json(task_dir / "control.json", control)
            run = await wait_ready(arga, run, task_dir=task_dir, control=control)
            control = control_payload(task_id, scenario_id, digest, run)
            write_private_json(task_dir / "control.json", control)

            queries = snapshot_queries_for_task(task)
            query_count = len(queries)
            capturer = TrustedStateCapturer(timeout_seconds=CAPTURE_TIMEOUT_SECONDS)
            baseline = await capturer.capture(
                control,
                roles=task_roles(task),
                snapshot_queries=queries,
            )
            baseline_gaps = snapshot_capture_contract_gaps(task, baseline, label="baseline")
            if baseline_gaps:
                raise RuntimeError("; ".join(baseline_gaps))
            write_private_json(task_dir / "baseline-state.json", baseline.artifact_payload())

            final = await capturer.capture(
                control,
                roles=task_roles(task),
                snapshot_queries=queries,
            )
            final_gaps = snapshot_capture_contract_gaps(task, final, label="final")
            if final_gaps:
                raise RuntimeError("; ".join(final_gaps))
            write_private_json(task_dir / "final-state.json", final.artifact_payload())

            before = canonicalize_argabench_snapshot(baseline)
            after = canonicalize_argabench_snapshot(final)
            raw_deltas = diff_trusted_states(baseline, final)
            semantic_mutations = diff_canonical_resources(before, after)
            canonical_resource_count = len(after)
            raw_delta_count = len(raw_deltas)
            semantic_mutation_count = len(semantic_mutations)
            write_private_json(
                task_dir / "capture-diff.json",
                {
                    "protocol": "argabench-snapshot-preflight-diff/1",
                    "raw_deltas": jsonable(raw_deltas),
                    "semantic_mutations": jsonable(semantic_mutations),
                },
            )
            if semantic_mutations:
                raise RuntimeError(
                    f"unchanged seed produced {len(semantic_mutations)} canonical semantic mutations"
                )
        except BaseException as caught:
            error = caught
        finally:
            if run is not None:
                try:
                    cleanup = await wait_cleanup(arga, run.run_id)
                except BaseException as cleanup_error:
                    cleanup = {"error_type": type(cleanup_error).__name__, "error": str(cleanup_error)}
                write_private_json(task_dir / "cleanup.json", cleanup)
            arga.close()

        cleanup_ok = (
            run is not None
            and bool(cleanup)
            and cleanup_payload_proves_inert(cleanup, expected_run_id=run.run_id)
        )
        passed = error is None and cleanup_ok
        result = {
            "protocol": "argabench-snapshot-preflight-task/1",
            "task_id": task_id,
            "domain": task["domain"],
            "scenario_id": scenario_id,
            "run_id": run.run_id if run is not None else None,
            "status": "passed" if passed else "failed",
            "query_count": query_count,
            "canonical_resource_count": canonical_resource_count,
            "raw_delta_count": raw_delta_count,
            "semantic_mutation_count": semantic_mutation_count,
            "cleanup_succeeded": cleanup_ok,
            "started_at": started_at,
            "finished_at": utc_now(),
            "latency_ms": round((monotonic() - started_clock) * 1000),
            "error_type": type(error).__name__ if error is not None else None,
            "error": str(error) if error is not None else None,
            "traceback": traceback.format_exception(error) if error is not None else None,
        }
        write_private_json(task_dir / "preflight-result.json", result)
        print(
            json.dumps(
                {
                    "task_id": task_id,
                    "status": result["status"],
                    "query_count": query_count,
                    "semantic_mutation_count": semantic_mutation_count,
                    "cleanup_succeeded": cleanup_ok,
                    "error_type": result["error_type"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return result


async def async_main(args: argparse.Namespace) -> int:
    suite: object = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    if not isinstance(suite, dict):
        raise ValueError("suite must be a JSON object")
    typed_suite = cast(dict[str, object], suite)
    tasks_raw = typed_suite.get("tasks")
    if not isinstance(tasks_raw, list):
        raise ValueError("ArgaBench preflight requires exactly 40 tasks")
    typed_tasks_raw = cast(list[object], tasks_raw)
    if len(typed_tasks_raw) != 40:
        raise ValueError("ArgaBench preflight requires exactly 40 tasks")
    tasks = [
        cast(dict[str, Any], task)
        for task in typed_tasks_raw
        if isinstance(task, dict)
    ]
    if len(tasks) != 40:
        raise ValueError("every ArgaBench task must be a JSON object")
    plan = preflight_plan(tasks)
    if args.plan_only:
        print(json.dumps({"task_count": len(plan), "plan": plan}, indent=2, sort_keys=True))
        return 0
    if not os.environ.get("ARGA_API_KEY"):
        raise ValueError("ARGA_API_KEY is required")

    output_root = cast(Path, args.output).resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    write_private_json(
        output_root / "preflight-config.json",
        {
            "protocol": "argabench-snapshot-preflight/1",
            "suite_id": typed_suite.get("suite_id"),
            "environment": os.environ.get("ARGA_API_URL", "https://api.argalabs.com"),
            "concurrency": args.concurrency,
            "model_invocations": 0,
            "source_sha256": {
                "suite": file_hash(SUITE_PATH),
                "preflight_runner": file_hash(Path(__file__)),
                "fair_grader": file_hash(FAIR_GRADER_PATH),
            },
            "plan": plan,
            "started_at": utc_now(),
        },
    )
    scenario_ids = await resolve_scenarios(tasks)
    write_private_json(output_root / "staging-scenarios.json", scenario_ids)
    semaphore = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(
        *(
            preflight_task(
                task,
                scenario_id=scenario_ids[cast(str, task["id"])],
                output_root=output_root,
                semaphore=semaphore,
            )
            for task in tasks
        )
    )
    statuses = Counter(cast(str, result["status"]) for result in results)
    summary: dict[str, Any] = {
        "protocol": "argabench-snapshot-preflight-summary/1",
        "suite_id": typed_suite.get("suite_id"),
        "task_count": len(results),
        "passed": statuses["passed"],
        "failed": statuses["failed"],
        "cleanups_succeeded": sum(result["cleanup_succeeded"] is True for result in results),
        "query_captures_verified": sum(
            int(result["query_count"]) * 2 for result in results if result["status"] == "passed"
        ),
        "semantic_mutations": sum(int(result["semantic_mutation_count"]) for result in results),
        "model_invocations": 0,
        "finished_at": utc_now(),
        "results": results,
    }
    write_private_json(output_root / "preflight-summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
    return 0 if statuses["passed"] == 40 and summary["cleanups_succeeded"] == 40 else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--concurrency", type=int, choices=range(1, 11), default=5)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    if not args.plan_only and args.output is None:
        parser.error("--output is required unless --plan-only is used")
    return args


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
