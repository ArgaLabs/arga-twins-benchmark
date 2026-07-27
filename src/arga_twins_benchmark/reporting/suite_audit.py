from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlsplit

AUDIT_PROTOCOL = "arga-bench-suite-audit/1"

_CONTROL_PLANE_FIRST_SEGMENTS = frozenset(
    {
        "_admin",
        "_control",
        "_twin",
        "_ui",
        "admin",
        "control",
        "control-plane",
        "control_plane",
        "inspect",
        "reset",
    }
)
_CLEAN_TERMINAL_STATUSES = frozenset(
    {
        "cancelled",
        "canceled",
        "expired",
        "torn_down",
        "terminated",
        "deleted",
        "cleaned_up",
        "failed",
    }
)
_SUITE_CHECK_NAMES = ("concurrency_history",)
_TRIAL_CHECK_NAMES = (
    "response_model",
    "prompt_hash",
    "no_fallback",
    "tool_call_minimum",
    "tool_call_count_consistency",
    "provider_trace_integrity",
    "provider_trace_destination",
    "provider_trace_control_plane",
    "cleanup_identity",
    "cleanup_inert",
    "state_grade_completeness",
)
_CHECK_NAMES = _SUITE_CHECK_NAMES + _TRIAL_CHECK_NAMES


class SuiteAuditError(ValueError):
    """Raised when the suite manifest or prompt ledger cannot be audited."""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _parse_aware_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _manifest_concurrency_audit(manifest: Mapping[str, Any]) -> dict[str, Any]:
    original = manifest.get("concurrency")
    initial = manifest.get("initial_concurrency", original)
    last_execution = manifest.get("last_execution_concurrency", initial)
    raw_history = manifest.get("concurrency_history")
    raw_runner_commits = manifest.get("runner_commits")
    issues: list[str] = []
    legacy_backfilled = False

    if (
        isinstance(original, bool)
        or not isinstance(original, int)
        or not 1 <= original <= 16
        or isinstance(initial, bool)
        or not isinstance(initial, int)
        or not 1 <= initial <= 16
    ):
        issues.append("concurrency and initial_concurrency must be integers between 1 and 16")
    elif original != initial:
        issues.append("initial_concurrency does not match the preserved concurrency")
    if isinstance(last_execution, bool) or not isinstance(last_execution, int) or not 1 <= last_execution <= 16:
        issues.append("last_execution_concurrency must be an integer between 1 and 16")

    runner_commit = manifest.get("runner_commit")
    if not isinstance(runner_commit, str) or not runner_commit:
        issues.append("runner_commit must be a non-empty string")

    runner_commit_items: list[str] = []
    if raw_runner_commits is None and isinstance(runner_commit, str) and runner_commit:
        runner_commit_items = [runner_commit]
        legacy_backfilled = True
    elif (
        not isinstance(raw_runner_commits, list)
        or not raw_runner_commits
        or not all(isinstance(commit, str) and commit for commit in cast(list[object], raw_runner_commits))
    ):
        issues.append("runner_commits must be an array of non-empty strings")
    else:
        runner_commit_items = list(cast(list[str], raw_runner_commits))
        if len(set(runner_commit_items)) != len(runner_commit_items):
            issues.append("runner_commits contains duplicates")
        if runner_commit_items and runner_commit_items[0] != runner_commit:
            issues.append("runner_commits does not begin with runner_commit")
    runner_commits = set(runner_commit_items)

    history: list[dict[str, Any]] = []
    if raw_history is None:
        created_at = manifest.get("created_at")
        created_timestamp = _parse_aware_timestamp(created_at)
        if (
            created_timestamp is None
            or not isinstance(created_at, str)
            or not isinstance(runner_commit, str)
            or not runner_commit
            or isinstance(initial, bool)
            or not isinstance(initial, int)
            or not 1 <= initial <= 16
        ):
            issues.append("legacy concurrency history cannot be safely backfilled")
        else:
            history.append(
                {
                    "event": "suite_created",
                    "concurrency": initial,
                    "recorded_at": created_at,
                    "runner_commit": runner_commit,
                    "backfilled_from_legacy": True,
                }
            )
            last_resumed_at = manifest.get("last_resumed_at")
            if last_resumed_at is not None:
                last_resumed_timestamp = _parse_aware_timestamp(last_resumed_at)
                if last_resumed_timestamp is None or last_resumed_timestamp < created_timestamp:
                    issues.append("legacy last_resumed_at is invalid or precedes created_at")
                else:
                    legacy_resume_commit = runner_commit_items[-1] if runner_commit_items else runner_commit
                    history.append(
                        {
                            "event": "suite_resumed",
                            "concurrency": last_execution,
                            "recorded_at": last_resumed_at,
                            "runner_commit": legacy_resume_commit,
                            "backfilled_from_legacy": True,
                        }
                    )
            legacy_backfilled = True
    elif not isinstance(raw_history, list) or not raw_history:
        issues.append("concurrency_history is missing or empty")
    else:
        for index, raw_entry in enumerate(cast(list[object], raw_history)):
            if not isinstance(raw_entry, dict):
                issues.append(f"concurrency_history[{index}] is not an object")
                continue
            entry = cast(dict[str, Any], raw_entry)
            concurrency = entry.get("concurrency")
            if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 16:
                issues.append(f"concurrency_history[{index}].concurrency is invalid")
            expected_event = "suite_created" if index == 0 else "suite_resumed"
            if entry.get("event") != expected_event:
                issues.append(f"concurrency_history[{index}].event must be {expected_event!r}")
            if not isinstance(entry.get("runner_commit"), str) or not entry["runner_commit"]:
                issues.append(f"concurrency_history[{index}].runner_commit is invalid")
            if _parse_aware_timestamp(entry.get("recorded_at")) is None:
                issues.append(f"concurrency_history[{index}].recorded_at is not an aware timestamp")
            if entry.get("backfilled_from_legacy") is True:
                legacy_backfilled = True
            history.append(entry)

    if history and history[0].get("concurrency") != initial:
        issues.append("first concurrency history entry does not preserve initial_concurrency")
    if history and history[-1].get("concurrency") != last_execution:
        issues.append("last concurrency history entry does not match last_execution_concurrency")
    if history and history[0].get("runner_commit") != runner_commit:
        issues.append("first concurrency history commit does not match runner_commit")
    timestamps = [_parse_aware_timestamp(entry.get("recorded_at")) for entry in history]
    valid_timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    if len(valid_timestamps) == len(history) and any(
        current < previous for previous, current in zip(valid_timestamps, valid_timestamps[1:], strict=False)
    ):
        issues.append("concurrency history timestamps are not chronological")
    created_timestamp = _parse_aware_timestamp(manifest.get("created_at"))
    if history and (created_timestamp is None or timestamps[0] != created_timestamp):
        issues.append("first concurrency history timestamp does not match created_at")
    last_resumed_at = manifest.get("last_resumed_at")
    if len(history) == 1 and last_resumed_at is not None:
        issues.append("last_resumed_at exists without a resume history entry")
    elif len(history) > 1:
        parsed_last_resumed_at = _parse_aware_timestamp(last_resumed_at)
        if parsed_last_resumed_at is None or timestamps[-1] != parsed_last_resumed_at:
            issues.append("last concurrency history timestamp does not match last_resumed_at")
    for index, entry in enumerate(history):
        commit = entry.get("runner_commit")
        if isinstance(commit, str) and commit not in runner_commits:
            issues.append(f"concurrency_history[{index}].runner_commit is absent from runner_commits")
    if history and not legacy_backfilled:
        ordered_history_commits = list(
            dict.fromkeys(
                cast(str, entry["runner_commit"])
                for entry in history
                if isinstance(entry.get("runner_commit"), str) and entry["runner_commit"]
            )
        )
        if ordered_history_commits != runner_commit_items:
            issues.append("runner_commits order does not match concurrency_history")

    return {
        "original_concurrency": original,
        "initial_concurrency": initial,
        "last_execution_concurrency": last_execution,
        "history_entries": len(history),
        "legacy_backfilled": legacy_backfilled,
        "valid": not issues,
        "issues": issues,
    }


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw: object = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise SuiteAuditError(f"{label} does not exist: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise SuiteAuditError(f"{label} is not readable JSON: {path}: {error}") from error
    if not isinstance(raw, dict):
        raise SuiteAuditError(f"{label} must be a JSON object: {path}")
    return cast(dict[str, Any], raw)


def _optional_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        raw: object = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        return None, str(error)
    if not isinstance(raw, dict):
        return None, "top-level JSON value is not an object"
    return cast(dict[str, Any], raw), None


def _required_string(mapping: Mapping[str, Any], key: str, *, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise SuiteAuditError(f"{label}.{key} must be a non-empty string")
    return value


def _model_id_from_plan(plan: Mapping[str, Any], *, label: str) -> str:
    model = plan.get("model")
    if not isinstance(model, dict):
        raise SuiteAuditError(f"{label}.model must be an object")
    return _required_string(cast(dict[str, Any], model), "model_id", label=f"{label}.model")


def _normalized_status(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _cleanup_run(payload: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if payload is None:
        return None
    raw = payload.get("twin_run")
    return cast(Mapping[str, Any], raw) if isinstance(raw, dict) else None


def _cleanup_is_inert(payload: Mapping[str, Any] | None, *, expected_run_id: str) -> bool:
    run = _cleanup_run(payload)
    if run is None or run.get("run_id") != expected_run_id:
        return False
    status = _normalized_status(run.get("status"))
    twins = run.get("twins")
    return status in _CLEAN_TERMINAL_STATUSES and isinstance(twins, dict) and not twins


def _decoded_first_segment(path: str) -> str | None:
    parsed = urlsplit(path)
    decoded_path = parsed.path
    for _ in range(5):
        next_path = unquote(decoded_path)
        if next_path == decoded_path:
            break
        decoded_path = next_path
    normalized = decoded_path.replace("\\", "/")
    segments = [segment.casefold() for segment in normalized.split("/") if segment]
    return segments[0] if segments else None


def _path_is_external_or_unsafe(path: object) -> bool:
    if not isinstance(path, str) or not path:
        return True
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or path.startswith("//"):
        return True
    decoded = path
    for _ in range(5):
        next_path = unquote(decoded)
        if next_path == decoded:
            break
        decoded = next_path
    normalized = decoded.replace("\\", "/")
    return normalized.startswith("//") or any(segment in {".", ".."} for segment in normalized.split("/"))


def _path_is_control_plane(path: object) -> bool:
    return isinstance(path, str) and _decoded_first_segment(path) in _CONTROL_PLANE_FIRST_SEGMENTS


def _ledger_index(
    ledger: Mapping[str, Any],
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, str]]]:
    raw_entries = ledger.get("entries")
    if not isinstance(raw_entries, list):
        raise SuiteAuditError("prompt ledger entries must be an array")
    entries = cast(list[object], raw_entries)

    index: dict[tuple[str, str], dict[str, Any]] = {}
    issues: list[dict[str, str]] = []
    for position, raw_entry in enumerate(entries):
        label = f"prompt ledger entry {position}"
        if not isinstance(raw_entry, dict):
            issues.append({"trial_id": "<ledger>", "detail": f"{label} is not an object"})
            continue
        entry = cast(dict[str, Any], raw_entry)
        instance_id = entry.get("instance_id")
        model_id = entry.get("model_id")
        if not isinstance(instance_id, str) or not isinstance(model_id, str):
            issues.append({"trial_id": "<ledger>", "detail": f"{label} is missing instance_id or model_id"})
            continue
        key = (instance_id, model_id)
        if key in index:
            issues.append(
                {
                    "trial_id": "<ledger>",
                    "detail": f"duplicate prompt ledger entry for instance={instance_id!r}, model={model_id!r}",
                }
            )
            continue
        index[key] = entry
        for prompt_name in ("system_prompt", "user_prompt"):
            prompt = entry.get(prompt_name)
            declared_hash = entry.get(f"{prompt_name}_sha256")
            if not isinstance(prompt, str) or not isinstance(declared_hash, str):
                issues.append(
                    {
                        "trial_id": "<ledger>",
                        "detail": f"{label} has invalid {prompt_name} or {prompt_name}_sha256",
                    }
                )
            elif _sha256_text(prompt) != declared_hash:
                issues.append(
                    {
                        "trial_id": "<ledger>",
                        "detail": f"{label} declares the wrong {prompt_name}_sha256",
                    }
                )
    return index, issues


def _new_check_totals() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "checked": 0,
            "passed_count": 0,
            "not_applicable": 0,
            "violations": [],
        }
        for name in _CHECK_NAMES
    }


def _record_pass(checks: dict[str, dict[str, Any]], check: str) -> None:
    checks[check]["checked"] += 1
    checks[check]["passed_count"] += 1


def _record_skip(checks: dict[str, dict[str, Any]], check: str) -> None:
    checks[check]["not_applicable"] += 1


def _record_violation(
    checks: dict[str, dict[str, Any]],
    violation_trials: dict[str, set[str]],
    *,
    check: str,
    trial_id: str,
    detail: str,
) -> None:
    checks[check]["checked"] += 1
    checks[check]["violations"].append({"trial_id": trial_id, "detail": detail})
    violation_trials[trial_id].add(check)


def _check_prompt(
    *,
    trial_id: str,
    trial_dir: Path,
    instance_id: str,
    model_id: str,
    prompt_required: bool,
    ledger_entry: Mapping[str, Any] | None,
    checks: dict[str, dict[str, Any]],
    violation_trials: dict[str, set[str]],
) -> None:
    if ledger_entry is None:
        _record_violation(
            checks,
            violation_trials,
            check="prompt_hash",
            trial_id=trial_id,
            detail=f"prompt ledger has no entry for instance={instance_id!r}, model={model_id!r}",
        )
        return

    prompt, error = _optional_json_object(trial_dir / "prompt.json")
    if error is not None:
        _record_violation(
            checks,
            violation_trials,
            check="prompt_hash",
            trial_id=trial_id,
            detail=f"prompt.json is invalid: {error}",
        )
        return
    if prompt is None:
        if prompt_required:
            _record_violation(
                checks,
                violation_trials,
                check="prompt_hash",
                trial_id=trial_id,
                detail="prompt.json is missing for a candidate invocation",
            )
        else:
            _record_skip(checks, "prompt_hash")
        return

    mismatches: list[str] = []
    for prompt_name in ("system_prompt", "user_prompt"):
        actual = prompt.get(prompt_name)
        expected = ledger_entry.get(prompt_name)
        expected_hash = ledger_entry.get(f"{prompt_name}_sha256")
        if not isinstance(actual, str):
            mismatches.append(f"{prompt_name} is missing")
        elif not isinstance(expected, str) or not isinstance(expected_hash, str):
            mismatches.append(f"ledger {prompt_name} is invalid")
        elif actual != expected or _sha256_text(actual) != expected_hash:
            mismatches.append(f"{prompt_name} does not match its ledger hash")
    raw_prompt_model = prompt.get("model")
    prompt_model = cast(dict[str, Any], raw_prompt_model) if isinstance(raw_prompt_model, dict) else None
    prompt_model_id = prompt_model.get("model_id") if prompt_model is not None else None
    if prompt_model_id != model_id:
        mismatches.append(f"prompt model is {prompt_model_id!r}, expected {model_id!r}")

    if mismatches:
        _record_violation(
            checks,
            violation_trials,
            check="prompt_hash",
            trial_id=trial_id,
            detail="; ".join(mismatches),
        )
    else:
        _record_pass(checks, "prompt_hash")


def _check_response_model(
    *,
    trial_id: str,
    trial_dir: Path,
    result: Mapping[str, Any],
    requested_model: str,
    checks: dict[str, dict[str, Any]],
    violation_trials: dict[str, set[str]],
) -> None:
    response_model = result.get("response_model")
    status = result.get("status")
    invocation, invocation_error = _optional_json_object(trial_dir / "invocation.json")
    observed_models: list[object] = []
    if isinstance(response_model, str):
        observed_models.append(response_model)
    if invocation_error is not None:
        _record_violation(
            checks,
            violation_trials,
            check="response_model",
            trial_id=trial_id,
            detail=f"invocation.json is invalid: {invocation_error}",
        )
        return
    if invocation is not None:
        raw_events = invocation.get("events")
        if isinstance(raw_events, list):
            for raw_event in cast(list[object], raw_events):
                if isinstance(raw_event, dict):
                    event = cast(dict[str, Any], raw_event)
                    if event.get("type") == "assistant_response":
                        observed_models.append(event.get("response_model"))

    if not observed_models:
        if status == "completed":
            _record_violation(
                checks,
                violation_trials,
                check="response_model",
                trial_id=trial_id,
                detail="completed result has no response_model evidence",
            )
        else:
            _record_skip(checks, "response_model")
        return

    mismatches = [model for model in observed_models if model != requested_model]
    if mismatches:
        _record_violation(
            checks,
            violation_trials,
            check="response_model",
            trial_id=trial_id,
            detail=f"observed response models {mismatches!r}; expected only {requested_model!r}",
        )
    else:
        _record_pass(checks, "response_model")


def _check_fallback(
    *,
    trial_id: str,
    trial_dir: Path,
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    checks: dict[str, dict[str, Any]],
    violation_trials: dict[str, set[str]],
) -> None:
    fallback_values: list[tuple[str, object]] = []
    raw_plan_model = plan.get("model")
    raw_result_model = result.get("model")
    plan_model = cast(dict[str, Any], raw_plan_model) if isinstance(raw_plan_model, dict) else None
    result_model = cast(dict[str, Any], raw_result_model) if isinstance(raw_result_model, dict) else None
    fallback_values.append(("suite plan", plan_model.get("fallback") if plan_model is not None else None))
    fallback_values.append(("trial result", result_model.get("fallback") if result_model is not None else None))
    invocation, error = _optional_json_object(trial_dir / "invocation.json")
    if error is not None:
        _record_violation(
            checks,
            violation_trials,
            check="no_fallback",
            trial_id=trial_id,
            detail=f"invocation.json is invalid: {error}",
        )
        return
    if invocation is not None:
        raw_config = invocation.get("config")
        config = cast(dict[str, Any], raw_config) if isinstance(raw_config, dict) else None
        fallback_values.append(("invocation", config.get("fallback") if config is not None else None))

    invalid = [(source, value) for source, value in fallback_values if value not in (False, None)]
    required_missing = [source for source, value in fallback_values[:2] if value is None]
    if invalid or required_missing:
        details: list[str] = []
        if invalid:
            details.append(f"fallback enabled in {invalid!r}")
        if required_missing:
            details.append(f"fallback declaration missing in {required_missing!r}")
        _record_violation(
            checks,
            violation_trials,
            check="no_fallback",
            trial_id=trial_id,
            detail="; ".join(details),
        )
    else:
        _record_pass(checks, "no_fallback")


def _trace_events(
    *,
    trial_id: str,
    trial_dir: Path,
    required: bool,
    checks: dict[str, dict[str, Any]],
    violation_trials: dict[str, set[str]],
) -> list[dict[str, Any]] | None:
    trace, error = _optional_json_object(trial_dir / "provider-trace.json")
    if error is not None:
        _record_violation(
            checks,
            violation_trials,
            check="provider_trace_integrity",
            trial_id=trial_id,
            detail=f"provider-trace.json is invalid: {error}",
        )
        return None
    if trace is None:
        if required:
            _record_violation(
                checks,
                violation_trials,
                check="provider_trace_integrity",
                trial_id=trial_id,
                detail="provider-trace.json is missing for a completed invocation",
            )
        return None
    if trace.get("protocol") not in (None, "arga-bench-provider-trace/1"):
        _record_violation(
            checks,
            violation_trials,
            check="provider_trace_integrity",
            trial_id=trial_id,
            detail="provider-trace.json has an unsupported protocol",
        )
        return None
    raw_events = trace.get("events")
    if not isinstance(raw_events, list):
        _record_violation(
            checks,
            violation_trials,
            check="provider_trace_integrity",
            trial_id=trial_id,
            detail="provider-trace.json events must be an array of objects",
        )
        return None
    events = cast(list[object], raw_events)
    if not all(isinstance(event, dict) for event in events):
        _record_violation(
            checks,
            violation_trials,
            check="provider_trace_integrity",
            trial_id=trial_id,
            detail="provider-trace.json events must be an array of objects",
        )
        return None
    typed_events = [cast(dict[str, Any], event) for event in events]
    issues: list[str] = []
    for index, event in enumerate(typed_events, start=1):
        sequence = event.get("sequence")
        if isinstance(sequence, bool) or sequence != index:
            issues.append(f"event {index} sequence is not contiguous and one-based")
        requested = event.get("requested_provider")
        provider = event.get("provider")
        method = event.get("method")
        path = event.get("path")
        operation = event.get("operation")
        operation_type = event.get("operation_type")
        status_code = event.get("status_code")
        error_value = event.get("error")
        if not isinstance(requested, str):
            issues.append(f"event {index} requested_provider is not a string")
        if provider is not None and (not isinstance(provider, str) or not provider):
            issues.append(f"event {index} provider is invalid")
        for field_name, value in (
            ("method", method),
            ("path", path),
            ("operation", operation),
            ("operation_type", operation_type),
        ):
            if value is not None and not isinstance(value, str):
                issues.append(f"event {index} {field_name} is not a string or null")
        if status_code is not None and (isinstance(status_code, bool) or not isinstance(status_code, int)):
            issues.append(f"event {index} status_code is not an integer or null")
        if error_value is not None and not isinstance(error_value, str):
            issues.append(f"event {index} error is not a string or null")
        if provider is None and (status_code is not None or not isinstance(error_value, str) or not error_value):
            issues.append(f"event {index} claims an unresolved provider was executed")
        for field_name in ("request_fingerprint", "action_fingerprint", "attempt_fingerprint"):
            value = event.get(field_name)
            if value is not None and (not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None):
                issues.append(f"event {index} {field_name} is invalid")
    if issues:
        _record_violation(
            checks,
            violation_trials,
            check="provider_trace_integrity",
            trial_id=trial_id,
            detail="; ".join(issues),
        )
        return None
    return typed_events


def _check_trace(
    *,
    trial_id: str,
    trial_dir: Path,
    result: Mapping[str, Any],
    events: list[dict[str, Any]] | None,
    minimum_tool_calls: int,
    checks: dict[str, dict[str, Any]],
    violation_trials: dict[str, set[str]],
) -> None:
    completed = result.get("status") == "completed"
    if events is None:
        for check in (
            "tool_call_minimum",
            "tool_call_count_consistency",
            "provider_trace_control_plane",
        ):
            if completed:
                _record_violation(
                    checks,
                    violation_trials,
                    check=check,
                    trial_id=trial_id,
                    detail="candidate trace evidence is unavailable",
                )
            else:
                _record_skip(checks, check)
        if not completed and "provider_trace_destination" not in violation_trials[trial_id]:
            _record_skip(checks, "provider_trace_destination")
        _record_skip(checks, "provider_trace_integrity")
        return

    if completed:
        if len(events) < minimum_tool_calls:
            _record_violation(
                checks,
                violation_trials,
                check="tool_call_minimum",
                trial_id=trial_id,
                detail=f"trace has {len(events)} provider calls; minimum is {minimum_tool_calls}",
            )
        else:
            _record_pass(checks, "tool_call_minimum")
        declared_count = result.get("tool_calls")
        if declared_count != len(events):
            _record_violation(
                checks,
                violation_trials,
                check="tool_call_count_consistency",
                trial_id=trial_id,
                detail=f"result declares {declared_count!r} calls but trace contains {len(events)}",
            )
        else:
            _record_pass(checks, "tool_call_count_consistency")
    else:
        _record_skip(checks, "tool_call_minimum")
        _record_skip(checks, "tool_call_count_consistency")

    access, access_error = _optional_json_object(trial_dir / "candidate-access.json")
    allowed_providers: set[str] = set()
    access_valid = access_error is None and access is not None and isinstance(access.get("provider_access"), dict)
    if access_valid:
        assert access is not None
        allowed_providers = set(cast(dict[str, Any], access["provider_access"]))
        _record_pass(checks, "provider_trace_integrity")
    else:
        detail = (
            f"candidate-access.json is invalid: {access_error}"
            if access_error is not None
            else "candidate-access.json is missing or has invalid provider_access"
        )
        _record_violation(
            checks,
            violation_trials,
            check="provider_trace_integrity",
            trial_id=trial_id,
            detail=detail,
        )
    destination_details: list[str] = []
    control_details: list[str] = []
    for index, event in enumerate(events, start=1):
        provider = event.get("provider")
        path = event.get("path")
        rejected_before_resolution = provider is None and event.get("status_code") is None
        if access_valid and not rejected_before_resolution and provider not in allowed_providers:
            destination_details.append(f"event {index} resolved provider {provider!r} is not provisioned")
        if _path_is_external_or_unsafe(path):
            destination_details.append(f"event {index} uses an external or unsafe path")
        if _path_is_control_plane(path):
            control_details.append(f"event {index} uses a control-plane path")
    if destination_details:
        _record_violation(
            checks,
            violation_trials,
            check="provider_trace_destination",
            trial_id=trial_id,
            detail="; ".join(destination_details),
        )
    else:
        _record_pass(checks, "provider_trace_destination")
    if control_details:
        _record_violation(
            checks,
            violation_trials,
            check="provider_trace_control_plane",
            trial_id=trial_id,
            detail="; ".join(control_details),
        )
    else:
        _record_pass(checks, "provider_trace_control_plane")


def _check_cleanup(
    *,
    trial_id: str,
    trial_dir: Path,
    result: Mapping[str, Any],
    checks: dict[str, dict[str, Any]],
    violation_trials: dict[str, set[str]],
) -> None:
    control, control_error = _optional_json_object(trial_dir / "control.json")
    cleanup, cleanup_error = _optional_json_object(trial_dir / "cleanup.json")
    expected_run_id = control.get("run_id") if control is not None else None
    cleanup_required = isinstance(expected_run_id, str) or result.get("status") == "completed"

    if control_error is not None:
        _record_violation(
            checks,
            violation_trials,
            check="cleanup_identity",
            trial_id=trial_id,
            detail=f"control.json is invalid: {control_error}",
        )
        _record_skip(checks, "cleanup_inert")
        return
    if not isinstance(expected_run_id, str) or not expected_run_id:
        if cleanup_required:
            _record_violation(
                checks,
                violation_trials,
                check="cleanup_identity",
                trial_id=trial_id,
                detail="control.json has no authoritative run_id",
            )
            _record_violation(
                checks,
                violation_trials,
                check="cleanup_inert",
                trial_id=trial_id,
                detail="cleanup cannot be proven without an authoritative run_id",
            )
        else:
            _record_skip(checks, "cleanup_identity")
            _record_skip(checks, "cleanup_inert")
        return

    if cleanup_error is not None:
        _record_violation(
            checks,
            violation_trials,
            check="cleanup_identity",
            trial_id=trial_id,
            detail=f"cleanup.json is invalid: {cleanup_error}",
        )
        _record_violation(
            checks,
            violation_trials,
            check="cleanup_inert",
            trial_id=trial_id,
            detail="cleanup evidence is invalid",
        )
        return

    evidence: list[tuple[str, Mapping[str, Any] | None]] = [
        ("cleanup.json", cleanup),
        (
            "result.cleanup",
            cast(Mapping[str, Any], result["cleanup"]) if isinstance(result.get("cleanup"), dict) else None,
        ),
    ]
    state, _ = _optional_json_object(trial_dir / "state.json")
    if state is not None and isinstance(state.get("cleanup"), dict):
        evidence.append(("state.cleanup", cast(Mapping[str, Any], state["cleanup"])))
    mismatches: list[str] = []
    for source, payload in evidence:
        run = _cleanup_run(payload)
        observed_run_id = run.get("run_id") if run is not None else None
        if observed_run_id != expected_run_id:
            mismatches.append(f"{source} run_id is {observed_run_id!r}")
    if mismatches:
        _record_violation(
            checks,
            violation_trials,
            check="cleanup_identity",
            trial_id=trial_id,
            detail=f"expected run_id {expected_run_id!r}; " + "; ".join(mismatches),
        )
    else:
        _record_pass(checks, "cleanup_identity")

    cleanup_succeeded = result.get("cleanup_succeeded") is True
    if _cleanup_is_inert(cleanup, expected_run_id=expected_run_id) and cleanup_succeeded:
        _record_pass(checks, "cleanup_inert")
    else:
        _record_violation(
            checks,
            violation_trials,
            check="cleanup_inert",
            trial_id=trial_id,
            detail=(
                "cleanup must match the authoritative run_id, have a clean terminal status, "
                "show twins={}, and set result.cleanup_succeeded=true"
            ),
        )


def _finalize_checks(checks: dict[str, dict[str, Any]]) -> None:
    for check in checks.values():
        violations = cast(list[object], check["violations"])
        check["violation_count"] = len(violations)
        check["passed"] = not violations
        check["coverage_complete"] = check["not_applicable"] == 0


def audit_suite(
    suite_dir: Path,
    *,
    prompt_ledger: Path | None = None,
    minimum_tool_calls: int = 6,
) -> dict[str, Any]:
    """Audit a saved matrix suite without making network calls or modifying artifacts."""

    if minimum_tool_calls < 1:
        raise SuiteAuditError("minimum_tool_calls must be positive")
    suite_dir = suite_dir.resolve()
    manifest = _read_json_object(suite_dir / "suite.json", label="suite manifest")
    ledger_path = (prompt_ledger or (suite_dir / "prompt-ledger.json")).resolve()
    ledger = _read_json_object(ledger_path, label="prompt ledger")
    ledger_entries, ledger_issues = _ledger_index(ledger)

    raw_plans = manifest.get("trials")
    if not isinstance(raw_plans, list) or not raw_plans:
        raise SuiteAuditError("suite manifest trials must be a non-empty array")
    plans: list[dict[str, Any]] = []
    for index, raw_plan in enumerate(cast(list[object], raw_plans)):
        if not isinstance(raw_plan, dict):
            raise SuiteAuditError(f"suite manifest trial {index} must be an object")
        plans.append(cast(dict[str, Any], raw_plan))

    checks = _new_check_totals()
    violation_trials: dict[str, set[str]] = defaultdict(set)
    concurrency_audit = _manifest_concurrency_audit(manifest)
    concurrency_issues = cast(list[str], concurrency_audit["issues"])
    if concurrency_issues:
        _record_violation(
            checks,
            violation_trials,
            check="concurrency_history",
            trial_id="<suite>",
            detail="; ".join(concurrency_issues),
        )
    else:
        _record_pass(checks, "concurrency_history")
    for issue in ledger_issues:
        _record_violation(
            checks,
            violation_trials,
            check="prompt_hash",
            trial_id=issue["trial_id"],
            detail=issue["detail"],
        )

    expected_ledger_keys: set[tuple[str, str]] = set()
    by_model_counts: dict[str, Counter[str]] = defaultdict(Counter)
    observed_results = 0
    terminal_results = 0
    completed_results = 0
    seen_trial_ids: set[str] = set()

    for index, plan in enumerate(plans):
        label = f"suite manifest trial {index}"
        trial_id = _required_string(plan, "trial_id", label=label)
        instance_id = _required_string(plan, "instance_id", label=label)
        requested_model = _model_id_from_plan(plan, label=label)
        if trial_id in seen_trial_ids:
            raise SuiteAuditError(f"suite manifest contains duplicate trial_id {trial_id!r}")
        seen_trial_ids.add(trial_id)
        expected_ledger_keys.add((instance_id, requested_model))
        by_model_counts[requested_model]["expected"] += 1

        trial_dir = suite_dir / "trials" / trial_id
        result, result_error = _optional_json_object(trial_dir / "result.json")
        if result_error is not None:
            status = "invalid_result"
            violation_trials[trial_id].add("suite_terminal")
        elif result is None:
            status = "missing_result"
            violation_trials[trial_id].add("suite_terminal")
        else:
            observed_results += 1
            raw_status = result.get("status")
            status = raw_status if isinstance(raw_status, str) and raw_status else "unknown"
            if result.get("terminal") is True:
                terminal_results += 1
            else:
                violation_trials[trial_id].add("suite_terminal")
            if status == "completed":
                completed_results += 1
        by_model_counts[requested_model][f"status:{status}"] += 1

        if result is None:
            for check_name in _TRIAL_CHECK_NAMES:
                _record_skip(checks, check_name)
            continue

        raw_result_model = result.get("model")
        result_model = cast(dict[str, Any], raw_result_model) if isinstance(raw_result_model, dict) else None
        result_model_id = result_model.get("model_id") if result_model is not None else None
        if result_model_id != requested_model:
            _record_violation(
                checks,
                violation_trials,
                check="response_model",
                trial_id=trial_id,
                detail=f"result requested model is {result_model_id!r}, expected {requested_model!r}",
            )
        else:
            _check_response_model(
                trial_id=trial_id,
                trial_dir=trial_dir,
                result=result,
                requested_model=requested_model,
                checks=checks,
                violation_trials=violation_trials,
            )

        prompt_required = result.get("invocation_started") is True or status == "completed"
        _check_prompt(
            trial_id=trial_id,
            trial_dir=trial_dir,
            instance_id=instance_id,
            model_id=requested_model,
            prompt_required=prompt_required,
            ledger_entry=ledger_entries.get((instance_id, requested_model)),
            checks=checks,
            violation_trials=violation_trials,
        )
        _check_fallback(
            trial_id=trial_id,
            trial_dir=trial_dir,
            plan=plan,
            result=result,
            checks=checks,
            violation_trials=violation_trials,
        )
        events = _trace_events(
            trial_id=trial_id,
            trial_dir=trial_dir,
            required=status == "completed",
            checks=checks,
            violation_trials=violation_trials,
        )
        _check_trace(
            trial_id=trial_id,
            trial_dir=trial_dir,
            result=result,
            events=events,
            minimum_tool_calls=minimum_tool_calls,
            checks=checks,
            violation_trials=violation_trials,
        )
        _check_cleanup(
            trial_id=trial_id,
            trial_dir=trial_dir,
            result=result,
            checks=checks,
            violation_trials=violation_trials,
        )
        if status == "completed":
            if result.get("state_grade_complete") is True:
                _record_pass(checks, "state_grade_completeness")
            else:
                _record_violation(
                    checks,
                    violation_trials,
                    check="state_grade_completeness",
                    trial_id=trial_id,
                    detail="completed result does not have state_grade_complete=true",
                )
        else:
            _record_skip(checks, "state_grade_completeness")

    for key in sorted(expected_ledger_keys - set(ledger_entries)):
        instance_id, model_id = key
        _record_violation(
            checks,
            violation_trials,
            check="prompt_hash",
            trial_id="<ledger>",
            detail=f"missing expected ledger key instance={instance_id!r}, model={model_id!r}",
        )
    for key in sorted(set(ledger_entries) - expected_ledger_keys):
        instance_id, model_id = key
        _record_violation(
            checks,
            violation_trials,
            check="prompt_hash",
            trial_id="<ledger>",
            detail=f"unexpected ledger key instance={instance_id!r}, model={model_id!r}",
        )

    _finalize_checks(checks)
    by_model: dict[str, dict[str, Any]] = {}
    for model_id, counts in sorted(by_model_counts.items()):
        statuses = {
            key.removeprefix("status:"): count for key, count in sorted(counts.items()) if key.startswith("status:")
        }
        by_model[model_id] = {
            "expected": counts["expected"],
            "statuses": statuses,
        }

    suite_complete = observed_results == len(plans) and terminal_results == len(plans)
    matrix_fully_evaluable = completed_results == len(plans)
    integrity_check_names = (
        "concurrency_history",
        "response_model",
        "prompt_hash",
        "no_fallback",
        "tool_call_count_consistency",
        "provider_trace_integrity",
        "cleanup_identity",
        "cleanup_inert",
    )
    integrity_passed = suite_complete and all(bool(checks[name]["passed"]) for name in integrity_check_names)
    benchmark_contract_passed = integrity_passed
    trajectory_diagnostics_passed = bool(checks["tool_call_minimum"]["passed"])
    destination_safety_passed = bool(
        checks["provider_trace_integrity"]["passed"]
        and checks["provider_trace_destination"]["passed"]
        and checks["provider_trace_control_plane"]["passed"]
    )
    scoring_ready = (
        benchmark_contract_passed and matrix_fully_evaluable and bool(checks["state_grade_completeness"]["passed"])
    )

    return {
        "protocol": AUDIT_PROTOCOL,
        "suite_run_id": manifest.get("suite_run_id"),
        "experiment_id": manifest.get("experiment_id"),
        "suite_dir": str(suite_dir),
        "prompt_ledger": str(ledger_path),
        "minimum_tool_calls": minimum_tool_calls,
        "concurrency": concurrency_audit,
        "expected_trials": len(plans),
        "observed_results": observed_results,
        "terminal_results": terminal_results,
        "completed_results": completed_results,
        "suite_complete": suite_complete,
        "matrix_fully_evaluable": matrix_fully_evaluable,
        "integrity_passed": integrity_passed,
        "benchmark_contract_passed": benchmark_contract_passed,
        "trajectory_diagnostics_passed": trajectory_diagnostics_passed,
        "destination_safety_passed": destination_safety_passed,
        "scoring_ready": scoring_ready,
        "by_model": by_model,
        "checks": checks,
        "violation_trials": {
            trial_id: sorted(check_names) for trial_id, check_names in sorted(violation_trials.items()) if check_names
        },
    }
