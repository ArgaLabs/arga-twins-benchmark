#!/usr/bin/env python3
"""Run the checked-in Cross-Functional 40 suite against saved Arga Scenarios."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import traceback
from collections import Counter
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, cast

from arga_twins_benchmark.agents import invoke_model
from arga_twins_benchmark.arga_cli import SubprocessArgaCli, TwinRun
from arga_twins_benchmark.evaluation.state_capture import TrustedStateCapturer, diff_trusted_states
from arga_twins_benchmark.lifecycle import (
    cleanup_instance,
    cleanup_payload_proves_inert,
    write_private_json,
)
from arga_twins_benchmark.providers import OfficialDocsGateway, OfficialDocsSnapshotCache, ProviderGateway
from arga_twins_benchmark.runner import SYSTEM_PROMPT

ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "benchmark" / "cross_functional_40" / "suite.json"
MODEL_MATRIX_PATH = ROOT / "benchmark" / "cross_functional_40" / "model_matrix.json"
SUITE_TAG = "suite:cross-functional-40-v1"
PROVIDER_TOOL_LIMIT = 60
OFFICIAL_DOCS_TOOL_LIMIT = 8
MODEL_TIMEOUT_SECONDS = 1_200
PROVISION_TIMEOUT_SECONDS = 1_200
POLL_SECONDS = 2.0
INVOCATION_STARTED_ARTIFACT = "model-invocation-started.json"
RETRY_ARCHIVE_DIR = "retry-archive"

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


@dataclass(frozen=True)
class ResumeDecision:
    action: str
    reason: str
    attempt: dict[str, Any] | None = None


@dataclass(frozen=True)
class TaskRunPlan:
    task: dict[str, Any]
    attempt_number: int
    archive_path: str | None = None


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return jsonable(asdict(value))
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    return value


def content_hash(bundle: dict[str, Any]) -> str:
    payload = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


def control_payload(task_id: str, scenario_id: str, digest: str, run: TwinRun) -> dict[str, Any]:
    return {
        "protocol": "arga-bench-control/1",
        "instance_id": task_id,
        "scenario_id": scenario_id,
        "scenario_content_sha256": digest,
        "run_id": run.run_id,
        "twin_run": dict(run.raw),
    }


def task_roles(task: dict[str, Any]) -> dict[str, str]:
    return {PROVIDER_ROLES[provider]: provider for provider in task["twins"]}


def load_profile(profile_id: str) -> dict[str, Any]:
    payload = json.loads(MODEL_MATRIX_PATH.read_text())
    profiles = payload.get("profiles")
    if not isinstance(profiles, list):
        raise ValueError("model matrix profiles must be an array")
    matches = [profile for profile in profiles if isinstance(profile, dict) and profile.get("id") == profile_id]
    if len(matches) != 1:
        available = ", ".join(sorted(str(profile.get("id")) for profile in profiles if isinstance(profile, dict)))
        raise ValueError(f"unknown profile {profile_id!r}; expected one of: {available}")
    return cast(dict[str, Any], matches[0])


def read_json_object(path: Path, *, required: bool = False) -> dict[str, Any] | None:
    if not path.is_file():
        if required:
            raise ValueError(f"required JSON artifact is missing: {path}")
        return None
    try:
        payload: object = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"JSON artifact is unreadable: {path}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must contain an object: {path}")
    return cast(dict[str, Any], payload)


def _trace_artifact_has_events(path: Path, key: str) -> bool:
    if not path.exists():
        return False
    try:
        payload = read_json_object(path, required=True)
    except ValueError:
        # An unreadable trace cannot prove that no model/tool activity occurred.
        return True
    assert payload is not None
    events = payload.get(key)
    return not isinstance(events, list) or bool(events)


def _attempt_has_invocation_evidence(attempt: dict[str, Any]) -> bool:
    if any(attempt.get(key) is not None for key in ("model_status", "response_model", "stop_reason")):
        return True
    if attempt.get("final_text") not in (None, ""):
        return True
    usage = attempt.get("usage")
    if isinstance(usage, dict) and usage:
        return True
    for key in ("output_tokens", "tool_calls", "provider_tool_calls", "official_docs_tool_calls"):
        value = attempt.get(key)
        if isinstance(value, (int, float)) and value != 0:
            return True
    return False


def _task_has_invocation_evidence(task_dir: Path, attempt: dict[str, Any] | None) -> bool:
    # invocation.json is an absolute no-retry boundary, even when malformed.
    if (task_dir / "invocation.json").exists():
        return True
    # New attempts persist this marker immediately before entering invoke_model,
    # closing the crash window between request dispatch and invocation.json.
    if (task_dir / INVOCATION_STARTED_ARTIFACT).exists():
        return True
    if attempt is not None and _attempt_has_invocation_evidence(attempt):
        return True
    if _trace_artifact_has_events(task_dir / "provider-trace.json", "events"):
        return True
    if _trace_artifact_has_events(task_dir / "official-docs-trace.json", "events"):
        return True
    if _trace_artifact_has_events(task_dir / "tool-steps.json", "steps"):
        return True
    # Older attempts predate the durable invocation marker. Once baseline capture
    # completed they may have entered the model call before being interrupted, so
    # absence of invocation.json alone is not sufficient proof for those attempts.
    return (task_dir / "baseline-state.json").exists()


def classify_resume_task(task_dir: Path, *, task_id: str, profile_id: str) -> ResumeDecision:
    if not task_dir.exists():
        return ResumeDecision("run", "task_not_started")
    if not task_dir.is_dir():
        return ResumeDecision("blocked", "task_path_is_not_directory")

    attempt_path = task_dir / "attempt.json"
    try:
        attempt = read_json_object(attempt_path) if attempt_path.exists() else None
    except ValueError:
        return ResumeDecision("blocked", "attempt_artifact_unreadable")

    if attempt is not None:
        if attempt.get("task_id") != task_id:
            return ResumeDecision("blocked", "attempt_task_mismatch", attempt)
        if attempt.get("profile_id") != profile_id:
            return ResumeDecision("blocked", "attempt_profile_mismatch", attempt)

    if _task_has_invocation_evidence(task_dir, attempt):
        return ResumeDecision("skip", "model_invocation_protected", attempt)
    if attempt is None:
        return ResumeDecision("run", "interrupted_before_attempt", None)
    if attempt.get("attempt_status") == "infrastructure_invalid":
        return ResumeDecision("run", "zero_invocation_infrastructure_invalid", attempt)
    return ResumeDecision("skip", "terminal_attempt_preserved", attempt)


def _control_run_id(task_dir: Path) -> str | None:
    control = read_json_object(task_dir / "control.json")
    if control is None:
        return None
    run_id = control.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError(f"control artifact has no run_id: {task_dir / 'control.json'}")
    return run_id


async def prove_prior_twin_inert(task_dir: Path) -> tuple[bool, dict[str, Any] | None]:
    try:
        run_id = _control_run_id(task_dir)
    except ValueError as error:
        return False, {"error_type": type(error).__name__, "error": str(error)}
    if run_id is None:
        attempt = read_json_object(task_dir / "attempt.json")
        if attempt is not None and attempt.get("run_id") is None:
            return True, {
                "outcome": "no_run_allocated",
                "reason": "infrastructure-invalid attempt recorded no twin run identity",
            }
        if not any(task_dir.iterdir()):
            return True, {"outcome": "interrupted_before_control_persisted"}
        return False, {
            "error_type": "UnsafeResumeBlocked",
            "error": "partial task artifacts have no control.json; prior twin identity is unknown",
        }

    try:
        existing_cleanup = read_json_object(task_dir / "cleanup.json")
    except ValueError:
        existing_cleanup = None
    if cleanup_payload_proves_inert(existing_cleanup, expected_run_id=run_id):
        return True, existing_cleanup

    try:
        cleanup = await cleanup_instance(task_dir / "control.json")
    except BaseException as error:
        cleanup = {"error_type": type(error).__name__, "error": str(error)}
    write_private_json(task_dir / "resume-cleanup.json", cleanup)
    return cleanup_payload_proves_inert(cleanup, expected_run_id=run_id), cleanup


def _next_archive_number(output_root: Path, task_id: str) -> int:
    task_archive = output_root / RETRY_ARCHIVE_DIR / task_id
    numbers = []
    for path in task_archive.glob("attempt-*"):
        suffix = path.name.removeprefix("attempt-")
        if suffix.isdigit():
            numbers.append(int(suffix))
    return max(numbers, default=0) + 1


def archive_retryable_task(
    *,
    output_root: Path,
    task_id: str,
    profile_id: str,
    decision: ResumeDecision,
    cleanup: dict[str, Any] | None,
) -> tuple[Path, int]:
    task_dir = output_root / "tasks" / task_id
    rechecked = classify_resume_task(task_dir, task_id=task_id, profile_id=profile_id)
    if rechecked.action != "run" or rechecked.reason != decision.reason:
        raise RuntimeError(
            f"resume eligibility changed for {profile_id}/{task_id}: "
            f"{decision.action}:{decision.reason} -> {rechecked.action}:{rechecked.reason}"
        )
    if not task_dir.exists():
        return task_dir, 1

    archive_number = _next_archive_number(output_root, task_id)
    archive_dir = output_root / RETRY_ARCHIVE_DIR / task_id / f"attempt-{archive_number:04d}"
    archive_dir.parent.mkdir(parents=True, exist_ok=True)
    if archive_dir.exists():
        raise RuntimeError(f"retry archive already exists: {archive_dir}")
    task_dir.rename(archive_dir)
    write_private_json(
        archive_dir / "archive-metadata.json",
        {
            "protocol": "arga-bench-cross-functional-retry-archive/1",
            "task_id": task_id,
            "profile_id": profile_id,
            "archive_number": archive_number,
            "archive_reason": decision.reason,
            "archived_at": utc_now(),
            "cleanup": cleanup,
        },
    )
    return task_dir, archive_number + 1


async def prepare_resume_task(
    *,
    output_root: Path,
    task: dict[str, Any],
    profile_id: str,
    semaphore: asyncio.Semaphore,
) -> tuple[TaskRunPlan | None, ResumeDecision, dict[str, Any] | None]:
    async with semaphore:
        task_id = str(task["id"])
        task_dir = output_root / "tasks" / task_id
        decision = classify_resume_task(task_dir, task_id=task_id, profile_id=profile_id)
        if decision.action != "run":
            return None, decision, None
        cleanup: dict[str, Any] | None = None
        if task_dir.exists():
            cleanup_ok, cleanup = await prove_prior_twin_inert(task_dir)
            if not cleanup_ok:
                blocked = ResumeDecision("blocked", "prior_twin_not_proven_inert", decision.attempt)
                write_private_json(
                    task_dir / "resume-blocked.json",
                    {
                        "protocol": "arga-bench-cross-functional-resume-blocked/1",
                        "task_id": task_id,
                        "profile_id": profile_id,
                        "reason": blocked.reason,
                        "cleanup": cleanup,
                        "recorded_at": utc_now(),
                    },
                )
                return None, blocked, cleanup
        _, attempt_number = archive_retryable_task(
            output_root=output_root,
            task_id=task_id,
            profile_id=profile_id,
            decision=decision,
            cleanup=cleanup,
        )
        archive_path = None
        if task_dir.exists():
            raise AssertionError(f"retry preparation did not clear active task directory: {task_dir}")
        if attempt_number > 1:
            archive_path = str(output_root / RETRY_ARCHIVE_DIR / task_id / f"attempt-{attempt_number - 1:04d}")
        return TaskRunPlan(task, attempt_number, archive_path), decision, cleanup


def estimated_cost(usage: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    cache_creation = usage.get("cache_creation")
    cache_5m = 0
    cache_1h = 0
    if isinstance(cache_creation, dict):
        cache_5m = int(cache_creation.get("ephemeral_5m_input_tokens", 0) or 0)
        cache_1h = int(cache_creation.get("ephemeral_1h_input_tokens", 0) or 0)
    input_rate = float(profile["input_usd_per_million"])
    output_rate = float(profile["output_usd_per_million"])
    cache_read_rate = float(profile["cache_read_usd_per_million"])
    cache_write_5m_rate = input_rate * 1.25 if profile["provider"] == "anthropic" else 0.0
    cache_write_1h_rate = input_rate * 2.0 if profile["provider"] == "anthropic" else 0.0
    # Anthropic reports uncached input separately. OpenAI and Google include cached tokens in input totals.
    billable_base_input = input_tokens if profile["provider"] == "anthropic" else max(0, input_tokens - cache_read)
    estimate = (
        billable_base_input * input_rate
        + output_tokens * output_rate
        + cache_read * cache_read_rate
        + cache_5m * cache_write_5m_rate
        + cache_1h * cache_write_1h_rate
    ) / 1_000_000
    return {
        "currency": "USD",
        "estimate": round(estimate, 8),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_write_5m_input_tokens": cache_5m,
        "cache_write_1h_input_tokens": cache_1h,
        "rates_usd_per_million_tokens": {
            "input": input_rate,
            "output": output_rate,
            "cache_read": cache_read_rate,
            "cache_write_5m": cache_write_5m_rate,
            "cache_write_1h": cache_write_1h_rate,
        },
        "pricing_source": profile["pricing_source"],
        "pricing_checked_on": "2026-08-15",
        "pricing_note": (
            "Gemini 3.1 Pro standard <=200k-token prompt tier"
            if profile["model_id"] == "gemini-3.1-pro-preview"
            else None
        ),
    }


async def wait_ready(
    arga: SubprocessArgaCli,
    run: TwinRun,
    *,
    on_status: Any,
) -> TwinRun:
    deadline = asyncio.get_running_loop().time() + PROVISION_TIMEOUT_SECONDS
    latest = run
    terminal = {"cancelled", "canceled", "expired", "failed", "error", "torn_down", "terminated"}
    while latest.status != "ready" and latest.status.casefold() not in terminal:
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"timed out waiting for twin run {latest.run_id}; status={latest.status}")
        await asyncio.sleep(POLL_SECONDS)
        latest = await arga.status(latest.run_id)
        on_status(latest)
    if latest.status != "ready":
        raise RuntimeError(f"twin run {latest.run_id} ended in status {latest.status!r}")
    return latest


async def wait_cleanup(arga: SubprocessArgaCli, run_id: str) -> dict[str, Any]:
    teardown: object
    try:
        teardown = dict(await arga.teardown(run_id))
    except Exception as error:  # cleanup evidence retains failures without hiding the attempt
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


def trace_artifacts(gateway: ProviderGateway, docs: OfficialDocsGateway) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for record in gateway.trace_records:
        item = cast(dict[str, Any], jsonable(record))
        item["kind"] = "provider_api"
        records.append(item)
    for record in docs.trace_records:
        item = cast(dict[str, Any], jsonable(record))
        item["kind"] = "provider_docs"
        records.append(item)
    records.sort(key=lambda item: (str(item.get("started_at", "")), str(item.get("kind", ""))))
    return tuple(records)


def concise_tool_steps(records: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for sequence, record in enumerate(records, start=1):
        step = {
            "sequence": sequence,
            "kind": record["kind"],
            "provider": record.get("provider") or record.get("requested_provider"),
            "status_code": record.get("status_code"),
            "latency_ms": record.get("latency_ms"),
            "error": record.get("error"),
        }
        if record["kind"] == "provider_api":
            step.update({"method": record.get("method"), "path": record.get("path")})
        else:
            step.update({"action": record.get("action"), "doc_id": record.get("doc_id")})
        steps.append(step)
    return steps


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
        content_tags = [tag for tag in tags if isinstance(tag, str) and tag.startswith("content-sha256:")]
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
        resolved[task["id"]] = scenario_id
    return resolved


async def run_task(
    task: dict[str, Any],
    *,
    scenario_id: str,
    output_root: Path,
    semaphore: asyncio.Semaphore,
    lifecycle_semaphore: asyncio.Semaphore,
    cleanup_semaphore: asyncio.Semaphore,
    docs_cache: OfficialDocsSnapshotCache,
    profile: dict[str, Any],
    attempt_number: int = 1,
) -> dict[str, Any]:
    async with semaphore:
        task_id = task["id"]
        task_dir = output_root / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=False)
        started_at = utc_now()
        started_clock = monotonic()
        digest = content_hash(task)
        roles = task_roles(task)
        run: TwinRun | None = None
        gateway: ProviderGateway | None = None
        docs: OfficialDocsGateway | None = None
        invocation: Any = None
        raw_deltas: list[Any] = []
        cleanup: dict[str, Any] = {}
        error: BaseException | None = None
        lifecycle_acquired = False

        arga = SubprocessArgaCli()
        try:
            await lifecycle_semaphore.acquire()
            lifecycle_acquired = True
            run = await arga.create_twin_run(
                twins=task["twins"],
                scenario_id=scenario_id,
                ttl_minutes=60,
            )
            write_private_json(task_dir / "control.json", control_payload(task_id, scenario_id, digest, run))
            run = await wait_ready(
                arga,
                run,
                on_status=lambda latest: write_private_json(
                    task_dir / "control.json", control_payload(task_id, scenario_id, digest, latest)
                ),
            )
            control = control_payload(task_id, scenario_id, digest, run)
            write_private_json(task_dir / "control.json", control)
            provider_access = run.candidate_access()
            gateway = ProviderGateway(
                provider_access,
                provider_roles=roles,
                max_calls=PROVIDER_TOOL_LIMIT,
                candidate_safe_surface=True,
            )
            docs = OfficialDocsGateway(
                provider_access.keys(),
                provider_roles=roles,
                max_calls=OFFICIAL_DOCS_TOOL_LIMIT,
                snapshot_cache=docs_cache,
            )
            tools = [gateway.tool_definition, docs.tool_definition]
            write_private_json(
                task_dir / "prompt.json",
                {
                    "protocol": "arga-bench-trial-prompt/1",
                    "profile_id": profile["id"],
                    "model": profile["model_id"],
                    "requested_effort": profile["requested_effort"],
                    "api_effort": profile["api_effort"],
                    "thinking": profile["thinking"],
                    "system_prompt": SYSTEM_PROMPT,
                    "user_prompt": task["prompt"],
                    "tool_definitions": tools,
                    "provider_tool_call_limit": PROVIDER_TOOL_LIMIT,
                    "official_docs_tool_call_limit": OFFICIAL_DOCS_TOOL_LIMIT,
                },
            )

            capturer = TrustedStateCapturer(timeout_seconds=60)
            baseline = await capturer.capture(control, roles=roles)
            write_private_json(task_dir / "baseline-state.json", baseline.artifact_payload())
            lifecycle_semaphore.release()
            lifecycle_acquired = False

            async def execute_tool(tool_name: str, tool_input: dict[str, Any]) -> object:
                if tool_name == gateway.tool_definition["name"]:
                    return await gateway.execute(tool_input)
                if tool_name == docs.tool_definition["name"]:
                    return await docs.execute(tool_input)
                return {"ok": False, "error": f"unknown tool {tool_name!r}"}

            write_private_json(
                task_dir / INVOCATION_STARTED_ARTIFACT,
                {
                    "protocol": "arga-bench-model-invocation-started/1",
                    "task_id": task_id,
                    "profile_id": profile["id"],
                    "attempt_number": attempt_number,
                    "started_at": utc_now(),
                },
            )
            invocation = await invoke_model(
                model_id=profile["model_id"],
                system_prompt=SYSTEM_PROMPT,
                user_prompt=task["prompt"],
                tool_schema=tools,
                execute_tool=execute_tool,
                max_tool_calls=PROVIDER_TOOL_LIMIT + OFFICIAL_DOCS_TOOL_LIMIT,
                timeout_seconds=MODEL_TIMEOUT_SECONDS,
                api_effort=profile["api_effort"],
                thinking=profile["thinking"],
            )
            write_private_json(task_dir / "invocation.json", invocation.as_dict())

            final_state = await capturer.capture(control, roles=roles)
            raw_deltas = diff_trusted_states(baseline, final_state)
            write_private_json(task_dir / "final-state.json", final_state.artifact_payload())
            write_private_json(
                task_dir / "raw-state-diff.json",
                {"protocol": "arga-bench-raw-state-diff/1", "deltas": jsonable(raw_deltas)},
            )
        except BaseException as caught:
            error = caught
        finally:
            if gateway is not None and docs is not None:
                traces = trace_artifacts(gateway, docs)
                provider_events = [item for item in traces if item["kind"] == "provider_api"]
                docs_events = [item for item in traces if item["kind"] == "provider_docs"]
                write_private_json(
                    task_dir / "provider-trace.json",
                    {"protocol": "arga-bench-provider-trace/1", "events": provider_events},
                )
                write_private_json(
                    task_dir / "official-docs-trace.json",
                    {"protocol": "arga-bench-official-docs-trace/1", "events": docs_events},
                )
                write_private_json(
                    task_dir / "tool-steps.json",
                    {"protocol": "arga-bench-tool-steps/1", "steps": concise_tool_steps(traces)},
                )
            if gateway is not None:
                await gateway.aclose()
            if docs is not None:
                await docs.aclose()
            if lifecycle_acquired:
                lifecycle_semaphore.release()
                lifecycle_acquired = False
            if run is not None:
                try:
                    async with cleanup_semaphore:
                        cleanup = await wait_cleanup(arga, run.run_id)
                except BaseException as cleanup_error:
                    cleanup = {"error_type": type(cleanup_error).__name__, "error": str(cleanup_error)}
                write_private_json(task_dir / "cleanup.json", cleanup)
            arga.close()

        usage = invocation.usage if invocation is not None else {}
        trace_steps = []
        if (task_dir / "tool-steps.json").is_file():
            trace_steps = json.loads((task_dir / "tool-steps.json").read_text())["steps"]
        delta_counts = Counter(delta.provider_name for delta in raw_deltas)
        transport_retries = 0
        if invocation is not None:
            transport_retries = sum(
                event.get("type") == "transport_error" and event.get("will_retry") is True
                for event in invocation.events
            )
        cleanup_ok = (
            run is not None and bool(cleanup) and cleanup_payload_proves_inert(cleanup, expected_run_id=run.run_id)
        )
        attempt = {
            "protocol": "arga-bench-cross-functional-attempt/2",
            "attempt_number": attempt_number,
            "task_id": task_id,
            "title": task["title"],
            "domain": task["domain"],
            "scenario_id": scenario_id,
            "run_id": run.run_id if run else None,
            "profile_id": profile["id"],
            "model_label": profile["label"],
            "model": profile["model_id"],
            "provider": profile["provider"],
            "requested_effort": profile["requested_effort"],
            "api_effort": profile["api_effort"],
            "thinking": profile["thinking"],
            "attempt_status": (
                "candidate_complete"
                if invocation is not None and invocation.status != "api_error"
                else "infrastructure_invalid"
            ),
            "model_status": invocation.status if invocation is not None else None,
            "response_model": invocation.response_model if invocation is not None else None,
            "stop_reason": invocation.stop_reason if invocation is not None else None,
            "prompt": task["prompt"],
            "prompt_sha256": prompt_hash(task["prompt"]),
            "prompt_matches_tasks_md": True,
            "seed_status": run.status if run else None,
            "started_at": started_at,
            "finished_at": utc_now(),
            "latency_ms": round((monotonic() - started_clock) * 1000),
            "final_text": invocation.final_text if invocation is not None else "",
            "usage": usage,
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "cost": estimated_cost(usage, profile),
            "tool_calls": len(trace_steps),
            "provider_tool_calls": sum(item["kind"] == "provider_api" for item in trace_steps),
            "official_docs_tool_calls": sum(item["kind"] == "provider_docs" for item in trace_steps),
            "tool_steps": trace_steps,
            "raw_state_delta_count": len(raw_deltas),
            "raw_state_delta_count_by_provider": dict(sorted(delta_counts.items())),
            "model_transport_retries": transport_retries,
            "cleanup_succeeded": cleanup_ok,
            "error_type": type(error).__name__ if error else None,
            "error": str(error) if error else None,
            "traceback": traceback.format_exception(error) if error else None,
        }
        write_private_json(task_dir / "attempt.json", attempt)
        print(
            json.dumps(
                {
                    "task_id": task_id,
                    "attempt_status": attempt["attempt_status"],
                    "model_status": attempt["model_status"],
                    "tool_calls": attempt["tool_calls"],
                    "output_tokens": attempt["output_tokens"],
                    "cost": attempt["cost"]["estimate"],
                    "cleanup_succeeded": cleanup_ok,
                    "error_type": attempt["error_type"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return attempt


def _run_config_payload(
    *,
    suite: dict[str, Any],
    profile: dict[str, Any],
    concurrency: int,
) -> dict[str, Any]:
    return {
        "protocol": "arga-bench-cross-functional-run/2",
        "suite_id": suite["suite_id"],
        "environment": os.environ.get("ARGA_API_URL", "https://api.argalabs.com"),
        "profile": profile,
        "concurrency": concurrency,
        "attempts_per_scenario": 1,
        "started_at": utc_now(),
    }


def validate_resume_run_config(
    output_root: Path,
    *,
    suite: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    config = read_json_object(output_root / "run-config.json", required=True)
    assert config is not None
    if config.get("protocol") != "arga-bench-cross-functional-run/2":
        raise ValueError("cannot resume: run-config protocol does not match Cross-Functional 40")
    if config.get("suite_id") != suite.get("suite_id"):
        raise ValueError("cannot resume: run-config suite identity changed")
    existing_profile = config.get("profile")
    if not isinstance(existing_profile, dict) or existing_profile.get("id") != profile.get("id"):
        raise ValueError("cannot resume: run-config profile identity changed")
    immutable_profile_fields = ("model_id", "provider", "requested_effort", "api_effort", "thinking")
    changed = [key for key in immutable_profile_fields if existing_profile.get(key) != profile.get(key)]
    if changed:
        raise ValueError(f"cannot resume: profile fields changed: {', '.join(changed)}")
    return config


def _load_current_attempts(output_root: Path, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for task in tasks:
        attempt = read_json_object(output_root / "tasks" / str(task["id"]) / "attempt.json")
        if attempt is not None:
            attempts.append(attempt)
    return attempts


def _write_resume_history(
    output_root: Path,
    *,
    concurrency: int,
    decisions: list[tuple[str, ResumeDecision]],
    plans: list[TaskRunPlan],
) -> None:
    history_path = output_root / "resume-history.json"
    history = read_json_object(history_path) or {
        "protocol": "arga-bench-cross-functional-resume-history/1",
        "entries": [],
    }
    entries = history.get("entries")
    if not isinstance(entries, list):
        raise ValueError("resume-history entries must be an array")
    entries.append(
        {
            "resumed_at": utc_now(),
            "concurrency": concurrency,
            "rerun_tasks": [str(plan.task["id"]) for plan in plans],
            "archived_attempts": [plan.archive_path for plan in plans if plan.archive_path is not None],
            "decisions": [
                {"task_id": task_id, "action": decision.action, "reason": decision.reason}
                for task_id, decision in decisions
            ],
        }
    )
    write_private_json(history_path, history)


async def async_main(args: argparse.Namespace) -> int:
    if not os.environ.get("ARGA_API_KEY"):
        raise ValueError("ARGA_API_KEY is required")
    profile = load_profile(args.profile)
    required_key = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google": "GEMINI_API_KEY",
    }[profile["provider"]]
    if not os.environ.get(required_key):
        raise ValueError(f"{required_key} is required")
    suite = json.loads(SUITE_PATH.read_text())
    tasks = suite["tasks"]
    if len(tasks) != 40:
        raise ValueError(f"expected 40 tasks, found {len(tasks)}")
    output_root = args.output.resolve()
    resume_existing = args.resume and output_root.exists()
    if resume_existing:
        validate_resume_run_config(output_root, suite=suite, profile=profile)
    else:
        output_root.mkdir(parents=True, exist_ok=False)
        write_private_json(
            output_root / "run-config.json",
            _run_config_payload(suite=suite, profile=profile, concurrency=args.concurrency),
        )
    scenario_ids = await resolve_scenarios(tasks)
    scenario_payload = {"suite_tag": SUITE_TAG, "scenario_ids": scenario_ids}
    existing_scenarios = read_json_object(output_root / "staging-scenarios.json")
    if existing_scenarios is not None and existing_scenarios != scenario_payload:
        raise ValueError("cannot resume: staging Scenario identities changed")
    if existing_scenarios is None:
        write_private_json(output_root / "staging-scenarios.json", scenario_payload)

    plans: list[TaskRunPlan]
    resume_decisions: list[tuple[str, ResumeDecision]] = []
    if resume_existing:
        preparation_semaphore = asyncio.Semaphore(args.lifecycle_concurrency)
        prepared = await asyncio.gather(
            *(
                prepare_resume_task(
                    output_root=output_root,
                    task=task,
                    profile_id=str(profile["id"]),
                    semaphore=preparation_semaphore,
                )
                for task in tasks
            )
        )
        plans = []
        for task, (plan, decision, _cleanup) in zip(tasks, prepared, strict=True):
            resume_decisions.append((str(task["id"]), decision))
            if plan is not None:
                plans.append(plan)
        _write_resume_history(
            output_root,
            concurrency=args.concurrency,
            decisions=resume_decisions,
            plans=plans,
        )
    else:
        plans = [TaskRunPlan(task, 1) for task in tasks]

    semaphore = asyncio.Semaphore(args.concurrency)
    lifecycle_semaphore = asyncio.Semaphore(args.lifecycle_concurrency)
    cleanup_semaphore = asyncio.Semaphore(args.cleanup_concurrency)
    docs_cache = OfficialDocsSnapshotCache()
    await asyncio.gather(
        *(
            run_task(
                plan.task,
                scenario_id=scenario_ids[plan.task["id"]],
                output_root=output_root,
                semaphore=semaphore,
                lifecycle_semaphore=lifecycle_semaphore,
                cleanup_semaphore=cleanup_semaphore,
                docs_cache=docs_cache,
                profile=profile,
                attempt_number=plan.attempt_number,
            )
            for plan in plans
        )
    )
    results = _load_current_attempts(output_root, tasks)
    summary = {
        "protocol": "arga-bench-cross-functional-run-summary/2",
        "suite_id": suite["suite_id"],
        "profile": profile,
        "concurrency": args.concurrency,
        "resumed": resume_existing,
        "tasks_rerun": len(plans) if resume_existing else 0,
        "resume_blocked": sum(decision.action == "blocked" for _, decision in resume_decisions),
        "protected_invocations": sum(
            decision.reason == "model_invocation_protected" for _, decision in resume_decisions
        ),
        "attempts": len(results),
        "candidate_complete": sum(item["attempt_status"] == "candidate_complete" for item in results),
        "model_completed": sum(item["model_status"] == "completed" for item in results),
        "cleanups_succeeded": sum(item["cleanup_succeeded"] is True for item in results),
        "estimated_cost_usd": round(sum(item["cost"]["estimate"] for item in results), 8),
        "input_tokens": sum(int(item["usage"].get("input_tokens", 0) or 0) for item in results),
        "output_tokens": sum(item["output_tokens"] for item in results),
        "tool_calls": sum(item["tool_calls"] for item in results),
        "provider_tool_calls": sum(item["provider_tool_calls"] for item in results),
        "official_docs_tool_calls": sum(item["official_docs_tool_calls"] for item in results),
        "finished_at": utc_now(),
    }
    write_private_json(output_root / "run-summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
    return (
        0
        if summary["attempts"] == 40
        and summary["candidate_complete"] == 40
        and summary["cleanups_succeeded"] == 40
        and summary["resume_blocked"] == 0
        else 1
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--concurrency", type=int, choices=range(1, 41), default=40)
    parser.add_argument("--lifecycle-concurrency", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--cleanup-concurrency", type=int, choices=range(1, 11), default=3)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an existing profile directory and retry only proven zero-invocation infrastructure failures.",
    )
    return parser.parse_args()


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
