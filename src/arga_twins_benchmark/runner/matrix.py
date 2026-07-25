from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import traceback
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from arga_twins_benchmark.agents import invoke_model
from arga_twins_benchmark.catalog import fingerprint_instance_bundle, validate_catalog
from arga_twins_benchmark.evaluation.deterministic import ToolCallRecord, evaluate_deterministic
from arga_twins_benchmark.evaluation.state_capture import (
    TrustedStateCapturer,
    TrustedStateSnapshot,
    diff_trusted_states,
)
from arga_twins_benchmark.lifecycle import cleanup_instance, provision_instance, write_private_json
from arga_twins_benchmark.providers import ProviderGateway
from arga_twins_benchmark.runner.prompting import (
    MODEL_PROFILES,
    SYSTEM_PROMPT,
    ModelProfile,
    compose_user_prompt,
    prompt_ledger_payload,
    render_prompt_ledger_markdown,
)
from arga_twins_benchmark.runner.state import EpisodeState
from arga_twins_benchmark.specs.models import (
    BindingSpec,
    ExperimentSpec,
    InstanceSpec,
    VerificationSpec,
    WorldSpec,
)


@dataclass(frozen=True)
class InstanceBundle:
    instance_path: Path
    instance: InstanceSpec
    binding: BindingSpec
    verification: VerificationSpec
    world: WorldSpec
    prompt: str


@dataclass(frozen=True)
class TrialPlan:
    suite_run_id: str
    trial_id: str
    repeat: int
    instance_id: str
    model: ModelProfile


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in cast(tuple[object, ...], value)]
    if isinstance(value, list):
        return [_jsonable(item) for item in cast(list[object], value)]
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item)
            for key, item in cast(dict[object, object], value).items()
        }
    return value


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _runner_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    raw: object = json.loads(path.read_text())
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else None


def _inferred_attempt_runner_commit(
    *,
    trial_dir: Path,
    output_root: Path,
    result: dict[str, Any] | None,
) -> tuple[str | None, str]:
    if result is not None and isinstance(result.get("runner_commit"), str):
        return cast(str, result["runner_commit"]), "trial_result"
    attempt = _read_json_object(trial_dir / "attempt.json")
    if attempt is not None and isinstance(attempt.get("runner_commit"), str):
        return cast(str, attempt["runner_commit"]), "attempt_metadata"
    suite = _read_json_object(output_root / "suite.json")
    if suite is not None and isinstance(suite.get("runner_commit"), str):
        return cast(str, suite["runner_commit"]), "suite_manifest"
    return None, "unknown"


def _cleanup_artifact_succeeded(trial_dir: Path, result: dict[str, Any] | None) -> bool:
    if result is not None and result.get("cleanup_succeeded") is True:
        return True
    cleanup = _read_json_object(trial_dir / "cleanup.json")
    return cleanup is not None and "error" not in cleanup


def _next_attempt_number(output_root: Path, trial_id: str) -> int:
    archive_root = output_root / "attempts" / trial_id
    archived_numbers = [
        int(match.group(1))
        for path in archive_root.glob("attempt-*")
        if (match := re.fullmatch(r"attempt-(\d+)", path.name)) is not None
    ]
    return max(archived_numbers, default=0) + 1


_RETRYABLE_INFRASTRUCTURE_ERROR_TYPES = frozenset(
    {
        "ArgaCliError",
        "CancelledError",
        "ConnectError",
        "ConnectTimeout",
        "KeyboardInterrupt",
        "NetworkError",
        "PoolTimeout",
        "ProxyError",
        "ReadError",
        "ReadTimeout",
        "RemoteProtocolError",
        "TimeoutError",
        "TimeoutExpired",
        "WriteError",
        "WriteTimeout",
        "_StateCaptureHttpError",
    }
)


def _retryable_infrastructure_result(result: dict[str, Any]) -> bool:
    if result.get("terminal") is not True or result.get("status") != "runtime_error":
        return False
    error_type = result.get("error_type")
    error = str(result.get("error", ""))
    if error_type == "_StateCaptureHttpError":
        return any(f"HTTP {status}" in error for status in (429, 502, 503, 504))
    if error_type == "StateCaptureError":
        return "request failed" in error
    return error_type in _RETRYABLE_INFRASTRUCTURE_ERROR_TYPES


async def _prepare_trial_attempt(
    *,
    output_root: Path,
    trial_id: str,
    runner_commit: str,
) -> tuple[Path, int, dict[str, Any] | None]:
    """Return a clean trial directory, preserving and cleaning any stale attempt."""

    trial_dir = output_root / "trials" / trial_id
    result = _read_json_object(trial_dir / "result.json")
    if (
        result is not None
        and result.get("terminal") is True
        and not _retryable_infrastructure_result(result)
    ):
        prior_commit, commit_source = _inferred_attempt_runner_commit(
            trial_dir=trial_dir,
            output_root=output_root,
            result=result,
        )
        prior_attempt = result.get("attempt")
        if not isinstance(prior_attempt, int) or prior_attempt < 1:
            prior_attempt = _next_attempt_number(output_root, trial_id)
        attempt_metadata = trial_dir / "attempt.json"
        if not attempt_metadata.exists():
            write_private_json(
                attempt_metadata,
                {
                    "protocol": "arga-bench-trial-attempt/1",
                    "attempt": prior_attempt,
                    "runner_commit": prior_commit,
                    "runner_commit_source": commit_source,
                    "terminal_result_preserved": True,
                },
            )
        return trial_dir, prior_attempt, result

    attempt_number = _next_attempt_number(output_root, trial_id)
    if not trial_dir.exists() or not any(trial_dir.iterdir()):
        trial_dir.mkdir(parents=True, exist_ok=True)
        return trial_dir, attempt_number, None

    cleanup_payload: dict[str, Any] | None = None
    control_path = trial_dir / "control.json"
    if not _cleanup_artifact_succeeded(trial_dir, result) and control_path.is_file():
        try:
            cleanup_payload = await cleanup_instance(control_path)
        except BaseException as cleanup_error:
            cleanup_payload = {
                "error_type": type(cleanup_error).__name__,
                "error": str(cleanup_error),
            }
        write_private_json(trial_dir / "resume-cleanup.json", cleanup_payload)
        if "error" in cleanup_payload:
            blocked = {
                "protocol": "arga-bench-trial-result/1",
                "terminal": True,
                "trial_id": trial_id,
                "status": "runtime_error",
                "error_type": "UnsafeResumeBlocked",
                "error": "prior attempt cleanup failed; a fresh twin was not provisioned",
                "cleanup": cleanup_payload,
                "cleanup_succeeded": False,
                "resume_blocked": True,
                "runner_commit": runner_commit,
                "finished_at": _utc_now(),
            }
            return trial_dir, attempt_number, blocked

    prior_commit, commit_source = _inferred_attempt_runner_commit(
        trial_dir=trial_dir,
        output_root=output_root,
        result=result,
    )
    archive_root = output_root / "attempts" / trial_id
    archive_root.mkdir(parents=True, exist_ok=True)
    archive_dir = archive_root / f"attempt-{attempt_number:04d}"
    if archive_dir.exists():
        raise RuntimeError(f"attempt archive already exists: {archive_dir}")
    trial_dir.rename(archive_dir)
    write_private_json(
        archive_dir / "attempt.json",
        {
            "protocol": "arga-bench-trial-attempt/1",
            "attempt": attempt_number,
            "runner_commit": prior_commit,
            "runner_commit_source": commit_source,
            "archived_at": _utc_now(),
            "archive_reason": (
                "runtime_error"
                if result is not None and result.get("status") == "runtime_error"
                else "interrupted_nonterminal_attempt"
            ),
            "resume_cleanup": cleanup_payload,
        },
    )
    trial_dir.mkdir(parents=True)
    return trial_dir, attempt_number + 1, None


def load_env_file(path: Path) -> list[str]:
    """Load a minimal KEY=VALUE env file without logging its values."""

    loaded: list[str] = []
    if not path.is_file():
        raise FileNotFoundError(path)
    for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"{path}:{line_number}: invalid environment variable name")
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def load_experiment_bundles(
    catalog_root: Path,
    experiment_id: str,
) -> tuple[ExperimentSpec, dict[str, InstanceBundle]]:
    documents = validate_catalog(catalog_root)
    experiment = next(
        (
            document.model
            for document in documents
            if isinstance(document.model, ExperimentSpec) and document.model.experiment_id == experiment_id
        ),
        None,
    )
    if experiment is None:
        raise ValueError(f"unknown experiment {experiment_id!r}")

    bindings = {
        document.model.binding_id: document.model
        for document in documents
        if isinstance(document.model, BindingSpec)
    }
    worlds = {
        document.model.world_id: document.model
        for document in documents
        if isinstance(document.model, WorldSpec)
    }
    verifications = {
        document.path.resolve(): document.model
        for document in documents
        if isinstance(document.model, VerificationSpec)
    }
    bundles: dict[str, InstanceBundle] = {}
    for document in documents:
        if not isinstance(document.model, InstanceSpec) or document.model.instance_id not in experiment.instances:
            continue
        instance = document.model
        verification_path = (document.path.parent / instance.verification_file).resolve()
        verification = verifications[verification_path]
        task_prompt = (document.path.parent / instance.prompt_file).read_text().strip()
        bundles[instance.instance_id] = InstanceBundle(
            instance_path=document.path,
            instance=instance,
            binding=bindings[instance.binding_id],
            verification=verification,
            world=worlds[instance.world_id],
            prompt=compose_user_prompt(task_prompt, verification.output_contract),
        )
    missing = set(experiment.instances) - set(bundles)
    if missing:
        raise AssertionError(f"validated catalog is missing instances: {sorted(missing)}")
    return experiment, bundles


def build_trial_plans(
    experiment: ExperimentSpec,
    *,
    suite_run_id: str,
    model_profiles: tuple[ModelProfile, ...],
    repeats: int,
) -> list[TrialPlan]:
    return [
        TrialPlan(
            suite_run_id=suite_run_id,
            trial_id=f"{suite_run_id}--r{repeat}--{instance_id}--{_slug(model.model_id)}",
            repeat=repeat,
            instance_id=instance_id,
            model=model,
        )
        for repeat in range(1, repeats + 1)
        for instance_id in experiment.instances
        for model in model_profiles
    ]


def _trace_call_records(gateway: ProviderGateway, bundle: InstanceBundle) -> list[ToolCallRecord]:
    reverse_roles = {provider: role for role, provider in bundle.binding.roles.items()}
    records: list[ToolCallRecord] = []
    for raw in gateway.trace_records:
        requested = getattr(raw, "requested_provider", None)
        resolved = getattr(raw, "provider", None)
        if not isinstance(resolved, str):
            resolved = getattr(raw, "resolved_provider", None)
        provider_role = (
            requested
            if isinstance(requested, str) and requested in bundle.binding.roles
            else reverse_roles.get(str(resolved), str(requested or resolved))
        )
        method = str(getattr(raw, "method", "")).upper()
        records.append(
            ToolCallRecord(
                provider_role=provider_role,
                method=method,
                path=str(getattr(raw, "path", "")),
                status_code=cast(int | None, getattr(raw, "status_code", None)),
                mutating=_trace_call_is_mutating(raw),
                operation=cast(str | None, getattr(raw, "operation", None)),
            )
        )
    return records


_READ_ONLY_POST_PATHS = (
    re.compile(r"^/v1/search(?:\?.*)?$"),
    re.compile(r"^/v1/(?:data_sources|databases)/[^/]+/query(?:\?.*)?$"),
    re.compile(
        r"^/api/(?:auth\.test|conversations\.(?:history|info|list|replies)|"
        r"search\.(?:all|files|messages)|users\.(?:info|list))(?:\?.*)?$"
    ),
)


def _trace_call_is_mutating(raw: object) -> bool:
    method = str(getattr(raw, "method", "")).upper()
    if method in {"", "GET"}:
        return False
    if method in {"PATCH", "PUT", "DELETE"}:
        return True
    if method != "POST":
        return True
    operation_type = getattr(raw, "operation_type", None)
    if operation_type == "query":
        return False
    if operation_type == "mutation":
        return True
    path = str(getattr(raw, "path", ""))
    return not any(pattern.fullmatch(path) for pattern in _READ_ONLY_POST_PATHS)


def _preliminary_grade(
    bundle: InstanceBundle,
    *,
    gateway: ProviderGateway,
    output: object,
) -> dict[str, Any]:
    """Grade trace and structured output while marking state grading incomplete."""

    trace = _trace_call_records(gateway, bundle)
    grade = evaluate_deterministic(
        bundle.verification,
        complexity=bundle.instance.complexity,
        resources=[],
        mutations=[],
        trace=trace,
        output=output,
    )
    trace_rule_ids = {
        rule.id for rule in bundle.verification.deterministic.trace_policy.required_calls
    } | {
        rule.id for rule in bundle.verification.deterministic.trace_policy.allowed_mutating_calls
    }
    trace_rule_ids.update(
        {
            "trace.minimum_tool_calls",
            "trace.distinct_and_causal_required_calls",
            "trace.forbidden_paths",
            "trace.provisioned_destinations_only",
            "trace.allowed_mutations",
            "output.contract",
        }
    )
    relevant = {
        key: passed
        for key, passed in grade.assertion_results.items()
        if key in trace_rule_ids
    }
    return {
        "protocol": "arga-bench-preliminary-grade/1",
        "complete": False,
        "reason": "trusted canonical final-state resources and mutation mapping are not attached",
        "trace_and_output_passed": bool(relevant) and all(relevant.values()),
        "assertion_results": relevant,
        "candidate_tool_calls": len(gateway.trace_records),
        "full_deterministic_grade": _jsonable(grade),
    }


async def run_trial(
    *,
    catalog_root: Path,
    bundle: InstanceBundle,
    plan: TrialPlan,
    output_root: Path,
    ttl_minutes: int = 60,
    runner_commit: str | None = None,
) -> dict[str, Any]:
    runner_commit = runner_commit or _runner_commit()
    trial_dir, attempt_number, existing_result = await _prepare_trial_attempt(
        output_root=output_root,
        trial_id=plan.trial_id,
        runner_commit=runner_commit,
    )
    if existing_result is not None:
        return existing_result
    result_path = trial_dir / "result.json"
    write_private_json(
        trial_dir / "attempt.json",
        {
            "protocol": "arga-bench-trial-attempt/1",
            "attempt": attempt_number,
            "runner_commit": runner_commit,
            "runner_commit_source": "current_process",
            "started_at": _utc_now(),
        },
    )
    control_path = trial_dir / "control.json"
    candidate_path = trial_dir / "candidate-access.json"
    state: dict[str, Any] = {
        "protocol": "arga-bench-trial-state/1",
        "trial_id": plan.trial_id,
        "suite_run_id": plan.suite_run_id,
        "instance_id": plan.instance_id,
        "model_id": plan.model.model_id,
        "repeat": plan.repeat,
        "attempt": attempt_number,
        "runner_commit": runner_commit,
        "phase": EpisodeState.MANIFEST_VALIDATED,
        "started_at": _utc_now(),
    }
    write_private_json(trial_dir / "state.json", state)
    cleanup_payload: object | None = None
    invocation_started = False
    gateway: ProviderGateway | None = None
    baseline_state: TrustedStateSnapshot | None = None
    final_state: TrustedStateSnapshot | None = None

    try:
        state["phase"] = EpisodeState.TWIN_RUN_REQUESTED
        write_private_json(trial_dir / "state.json", state)
        await provision_instance(
            catalog_root=catalog_root,
            instance_id=plan.instance_id,
            control_output=control_path,
            candidate_output=candidate_path,
            ttl_minutes=ttl_minutes,
            timeout_seconds=600,
        )
        state["phase"] = EpisodeState.TWINS_READY_AND_SEEDED
        write_private_json(trial_dir / "state.json", state)

        candidate_payload: object = json.loads(candidate_path.read_text())
        control_payload: object = json.loads(control_path.read_text())
        if not isinstance(candidate_payload, dict):
            raise ValueError("candidate access payload must be an object")
        if not isinstance(control_payload, dict):
            raise ValueError("control payload must be an object")
        candidate_mapping = cast(dict[str, object], candidate_payload)
        control_mapping = cast(dict[str, Any], control_payload)
        provider_access = candidate_mapping.get("provider_access")
        if not isinstance(provider_access, dict):
            raise ValueError("candidate access is missing provider_access")
        gateway = ProviderGateway(
            cast(dict[str, dict[str, object]], provider_access),
            provider_roles=bundle.binding.roles,
        )
        prompt_payload = {
            "protocol": "arga-bench-trial-prompt/1",
            "model": asdict(plan.model),
            "attempt": attempt_number,
            "runner_commit": runner_commit,
            "system_prompt": SYSTEM_PROMPT,
            "user_prompt": bundle.prompt,
            "tool_definition": gateway.tool_definition,
        }
        write_private_json(trial_dir / "prompt.json", prompt_payload)
        state_capturer = TrustedStateCapturer()
        baseline_state = await state_capturer.capture(
            control_mapping,
            roles=bundle.binding.roles,
            snapshot_queries=bundle.verification.deterministic.snapshot_queries,
        )
        write_private_json(trial_dir / "baseline-state.json", baseline_state.artifact_payload())
        state["phase"] = EpisodeState.BASELINE_CAPTURED
        write_private_json(trial_dir / "state.json", state)

        state["phase"] = EpisodeState.INVOCATION_STARTED
        state["invocation_started_at"] = _utc_now()
        write_private_json(trial_dir / "state.json", state)
        invocation_started = True

        async def execute_candidate_tool(tool_name: str, tool_input: dict[str, Any]) -> object:
            if tool_name != gateway.tool_definition["name"]:
                return {"ok": False, "error": f"unknown tool {tool_name!r}"}
            return await gateway.execute(tool_input)

        invocation = await invoke_model(
            model_id=plan.model.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=bundle.prompt,
            tool_schema=gateway.tool_definition,
            execute_tool=execute_candidate_tool,
            max_tool_calls=bundle.instance.budget.max_tool_calls,
            timeout_seconds=bundle.instance.budget.timeout_seconds,
        )
        state["phase"] = EpisodeState.INVOCATION_FINISHED
        state["invocation_finished_at"] = _utc_now()
        write_private_json(trial_dir / "state.json", state)
        invocation_payload = cast(dict[str, Any], _jsonable(invocation))
        write_private_json(trial_dir / "invocation.json", invocation_payload)
        write_private_json(
            trial_dir / "provider-trace.json",
            {
                "protocol": "arga-bench-provider-trace/1",
                "events": cast(list[object], _jsonable(gateway.trace_records)),
            },
        )

        final_text = getattr(invocation, "final_text", "")
        final_state = await state_capturer.capture(
            control_mapping,
            roles=bundle.binding.roles,
            snapshot_queries=bundle.verification.deterministic.snapshot_queries,
        )
        raw_state_deltas = diff_trusted_states(baseline_state, final_state)
        write_private_json(trial_dir / "final-state.json", final_state.artifact_payload())
        write_private_json(
            trial_dir / "raw-state-diff.json",
            {
                "protocol": "arga-bench-raw-state-diff/1",
                "deltas": cast(list[object], _jsonable(raw_state_deltas)),
            },
        )
        preliminary = _preliminary_grade(bundle, gateway=gateway, output=final_text)
        preliminary["raw_state_capture_complete"] = True
        preliminary["raw_state_delta_count"] = len(raw_state_deltas)
        write_private_json(trial_dir / "preliminary-grade.json", preliminary)
        state["phase"] = EpisodeState.FINAL_CAPTURED
        write_private_json(trial_dir / "state.json", state)

        result: dict[str, Any] = {
            "protocol": "arga-bench-trial-result/1",
            "terminal": True,
            "trial_id": plan.trial_id,
            "suite_run_id": plan.suite_run_id,
            "instance_id": plan.instance_id,
            "episode_hash": fingerprint_instance_bundle(catalog_root, plan.instance_id),
            "model": asdict(plan.model),
            "attempt": attempt_number,
            "runner_commit": runner_commit,
            "response_model": getattr(invocation, "response_model", None),
            "status": getattr(invocation, "status", None),
            "stop_reason": getattr(invocation, "stop_reason", None),
            "final_text": final_text,
            "tool_calls": len(gateway.trace_records),
            "usage": _jsonable(getattr(invocation, "usage", {})),
            "preliminary_grade": preliminary,
            "state_grade_complete": False,
            "invocation_started": invocation_started,
            "started_at": state["started_at"],
            "finished_at": _utc_now(),
        }
    except BaseException as error:
        result = {
            "protocol": "arga-bench-trial-result/1",
            "terminal": True,
            "trial_id": plan.trial_id,
            "suite_run_id": plan.suite_run_id,
            "instance_id": plan.instance_id,
            "model": asdict(plan.model),
            "attempt": attempt_number,
            "runner_commit": runner_commit,
            "status": "runtime_error",
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
            "invocation_started": invocation_started,
            "started_at": state["started_at"],
            "finished_at": _utc_now(),
        }
    finally:
        if gateway is not None:
            with suppress(Exception):
                await gateway.aclose()
        if control_path.is_file():
            try:
                cleanup_payload = await cleanup_instance(control_path)
            except BaseException as cleanup_error:
                cleanup_payload = {
                    "error_type": type(cleanup_error).__name__,
                    "error": str(cleanup_error),
                }
            write_private_json(trial_dir / "cleanup.json", cast(dict[str, Any], cleanup_payload))
        state["phase"] = EpisodeState.COMPLETE
        state["finished_at"] = _utc_now()
        state["cleanup"] = cleanup_payload
        write_private_json(trial_dir / "state.json", state)

    result["cleanup"] = cleanup_payload
    result["cleanup_succeeded"] = isinstance(cleanup_payload, dict) and "error" not in cleanup_payload
    write_private_json(result_path, result)
    return result


async def run_experiment_matrix(
    *,
    catalog_root: Path,
    experiment_id: str,
    output_root: Path,
    model_profiles: tuple[ModelProfile, ...] = MODEL_PROFILES,
    repeats: int = 1,
    concurrency: int = 4,
    ttl_minutes: int = 60,
    suite_run_id: str | None = None,
) -> dict[str, Any]:
    experiment, bundles = load_experiment_bundles(catalog_root, experiment_id)
    suite_run_id = suite_run_id or (
        f"{experiment_id}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    )
    suite_dir = output_root / suite_run_id
    suite_dir.mkdir(parents=True, exist_ok=True)
    ledger = prompt_ledger_payload(catalog_root, experiment_id, model_profiles=model_profiles)
    write_private_json(suite_dir / "prompt-ledger.json", ledger)
    markdown_path = suite_dir / "prompt-ledger.md"
    markdown_path.write_text(render_prompt_ledger_markdown(ledger))
    markdown_path.chmod(0o600)
    plans = build_trial_plans(
        experiment,
        suite_run_id=suite_run_id,
        model_profiles=model_profiles,
        repeats=repeats,
    )
    current_runner_commit = _runner_commit()
    manifest: dict[str, Any] = {
        "protocol": "arga-bench-suite/1",
        "suite_run_id": suite_run_id,
        "experiment_id": experiment_id,
        "runner_commit": current_runner_commit,
        "runner_commits": [current_runner_commit],
        "created_at": _utc_now(),
        "repeats": repeats,
        "concurrency": concurrency,
        "trial_count": len(plans),
        "models": [asdict(profile) for profile in model_profiles],
        "trials": [asdict(plan) for plan in plans],
    }
    manifest_path = suite_dir / "suite.json"
    if manifest_path.is_file():
        existing_manifest: object = json.loads(manifest_path.read_text())
        if not isinstance(existing_manifest, dict):
            raise ValueError("existing suite manifest must be an object")
        existing_mapping = cast(dict[str, Any], existing_manifest)
        immutable_fields = ("suite_run_id", "experiment_id", "repeats", "concurrency", "trial_count", "models")
        mismatched = [
            field for field in immutable_fields if existing_mapping.get(field) != manifest.get(field)
        ]
        if mismatched:
            raise ValueError(
                f"cannot resume suite {suite_run_id!r}; manifest fields changed: {', '.join(mismatched)}"
            )
        revisions = existing_mapping.get("runner_commits")
        if not isinstance(revisions, list):
            revisions = [existing_mapping.get("runner_commit")]
        runner_commits = [
            revision
            for revision in cast(list[object], revisions)
            if isinstance(revision, str)
        ]
        if current_runner_commit not in runner_commits:
            runner_commits.append(current_runner_commit)
        existing_mapping["runner_commits"] = runner_commits
        existing_mapping["last_resumed_at"] = _utc_now()
        manifest = existing_mapping
        write_private_json(manifest_path, manifest)
    else:
        write_private_json(manifest_path, manifest)

    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(plan: TrialPlan) -> dict[str, Any]:
        async with semaphore:
            print(
                json.dumps(
                    {
                        "event": "trial_started",
                        "trial_id": plan.trial_id,
                        "instance_id": plan.instance_id,
                        "model_id": plan.model.model_id,
                        "at": _utc_now(),
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
            result = await run_trial(
                catalog_root=catalog_root,
                bundle=bundles[plan.instance_id],
                plan=plan,
                output_root=suite_dir,
                ttl_minutes=ttl_minutes,
                runner_commit=current_runner_commit,
            )
            print(
                json.dumps(
                    {
                        "event": "trial_finished",
                        "trial_id": plan.trial_id,
                        "instance_id": plan.instance_id,
                        "model_id": plan.model.model_id,
                        "status": result.get("status"),
                        "cleanup_succeeded": result.get("cleanup_succeeded"),
                        "at": _utc_now(),
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
            return result

    results = await asyncio.gather(*(bounded(plan) for plan in plans))
    summary = {
        "protocol": "arga-bench-suite-summary/1",
        "suite_run_id": suite_run_id,
        "trial_count": len(results),
        "terminal_count": sum(result.get("terminal") is True for result in results),
        "runtime_error_count": sum(result.get("status") == "runtime_error" for result in results),
        "cleanup_failure_count": sum(result.get("cleanup_succeeded") is not True for result in results),
        "trace_output_pass_count": sum(
            isinstance(result.get("preliminary_grade"), dict)
            and cast(dict[str, Any], result["preliminary_grade"]).get("trace_and_output_passed") is True
            for result in results
        ),
        "state_grade_complete": False,
        "completed_at": _utc_now(),
        "results": results,
    }
    write_private_json(suite_dir / "summary.json", summary)
    return summary
