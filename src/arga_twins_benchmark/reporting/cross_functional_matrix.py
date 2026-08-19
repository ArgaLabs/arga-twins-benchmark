from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

from arga_twins_benchmark.lifecycle import cleanup_payload_proves_inert, write_private_json
from arga_twins_benchmark.reporting.cross_functional_tool_ceiling import (
    legacy_gateway_ceiling_rejections,
)

CROSS_FUNCTIONAL_MATRIX_CLASSIFICATION_PROTOCOL = "arga-bench-cross-functional-model-matrix-offline-classification/1"
HISTORICAL_CALIBRATION_PROTOCOL = "arga-bench-cross-functional-historical-calibration/1"

type ExecutionClass = Literal["exact_completed", "model_terminal", "infrastructure_invalid"]
type TrialValidity = Literal["valid", "invalid_infrastructure", "invalid_grader"]

_ATTEMPT_PROTOCOL = "arga-bench-cross-functional-attempt/2"
_CONTROL_PROTOCOL = "arga-bench-control/1"
_MATRIX_CONFIG_PROTOCOL = "arga-bench-cross-functional-model-matrix-run/1"
_PROFILE_CONFIG_PROTOCOL = "arga-bench-cross-functional-run/2"
_PROMPT_PROTOCOL = "arga-bench-trial-prompt/1"
_RAW_DIFF_PROTOCOL = "arga-bench-raw-state-diff/1"
_PROVIDER_TRACE_PROTOCOL = "arga-bench-provider-trace/1"
_DOCS_TRACE_PROTOCOL = "arga-bench-official-docs-trace/1"
_TOOL_STEPS_PROTOCOL = "arga-bench-tool-steps/1"
_RETRY_ARCHIVE_PROTOCOL = "arga-bench-cross-functional-retry-archive/1"

_PROFILE_IDENTITY_FIELDS = (
    "id",
    "label",
    "provider",
    "model_id",
    "requested_effort",
    "api_effort",
    "thinking",
)
_MODEL_TERMINAL_STATUSES = frozenset({"timed_out", "tool_limit_exceeded", "refused"})
_KNOWN_MODEL_STATUSES = _MODEL_TERMINAL_STATUSES | {
    "completed",
    "api_error",
    "incomplete",
    "invalid_response",
}
_TASK_ARTIFACTS = (
    "attempt.json",
    "baseline-state.json",
    "cleanup.json",
    "control.json",
    "final-state.json",
    "invocation.json",
    "official-docs-trace.json",
    "prompt.json",
    "provider-trace.json",
    "raw-state-diff.json",
    "tool-steps.json",
)


class CrossFunctionalMatrixClassificationError(ValueError):
    """Raised when trusted classifier inputs cannot define a grading schedule."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_path(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as error:
        raise CrossFunctionalMatrixClassificationError(f"cannot hash trusted artifact {path}: {error}") from error


def _load_trusted_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CrossFunctionalMatrixClassificationError(f"cannot read {label} {path}: {error}") from error
    if not isinstance(payload, dict):
        raise CrossFunctionalMatrixClassificationError(f"{label} {path} must contain a JSON object")
    return cast(dict[str, Any], payload)


def _read_artifact(path: Path, *, name: str, issues: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        issues.append(f"missing_artifact:{name}")
        return None
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        issues.append(f"unreadable_json:{name}")
        return None
    if not isinstance(payload, dict):
        issues.append(f"non_object_json:{name}")
        return None
    return cast(dict[str, Any], payload)


def _artifact_hashes(task_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name in _TASK_ARTIFACTS:
        path = task_dir / name
        if not path.is_file():
            continue
        try:
            hashes[name] = _sha256_bytes(path.read_bytes())
        except OSError:
            continue
    return hashes


def _content_hash(task: Mapping[str, Any]) -> str:
    payload = json.dumps(task, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(payload)


# These two Scenario identities predate verifier-only fairness revisions.  Their
# candidate-visible prompt, twins, and seed are unchanged; only hidden scoring
# metadata changed.  Preserve the recorded identity for saved-trial integrity
# without accepting arbitrary historical hashes.
_VERIFICATION_ONLY_LEGACY_CONTENT_HASHES: dict[str, frozenset[str]] = {
    "IT-01": frozenset(
        {
            "88a6cbd7a2602fa78219ab7b0b88bd8ae873d4976f61ca1a5abfdb32a441c56b",
            "7b66b98107c8e88c6e64117990600ff85c7ce60cf7156776f5dd6ee34df950eb",
        }
    ),
    "DEV-05": frozenset({"5467e4b5f2e58fc296e4d6b5b0c89cb9d0fe7ad06c4dab806d18a0575d484a47"}),
    "MKT-01": frozenset({"9205835e69125c1148dc8eb440ef716a21d79a7c54dc8e3f33d7606382949b7e"}),
}


def _accepted_content_hashes(task: Mapping[str, Any]) -> frozenset[str]:
    task_id = task.get("id")
    legacy = _VERIFICATION_ONLY_LEGACY_CONTENT_HASHES.get(task_id, frozenset())
    return legacy | {_content_hash(task)}


def _prompt_hash(prompt: str) -> str:
    return _sha256_bytes(prompt.encode("utf-8"))


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _profile_identity_issues(
    actual: Mapping[str, Any] | None,
    expected: Mapping[str, Any],
    *,
    prefix: str,
) -> list[str]:
    if actual is None:
        return [f"{prefix}:missing_profile"]
    return [
        f"{prefix}:mismatched_{field}" for field in _PROFILE_IDENTITY_FIELDS if actual.get(field) != expected.get(field)
    ]


def _attempt_profile_issues(attempt: Mapping[str, Any], profile: Mapping[str, Any]) -> list[str]:
    fields = {
        "profile_id": "id",
        "model_label": "label",
        "provider": "provider",
        "model": "model_id",
        "requested_effort": "requested_effort",
        "api_effort": "api_effort",
        "thinking": "thinking",
    }
    return [
        f"attempt_profile:mismatched_{attempt_field}"
        for attempt_field, profile_field in fields.items()
        if attempt.get(attempt_field) != profile.get(profile_field)
    ]


def _prompt_profile_issues(prompt: Mapping[str, Any], profile: Mapping[str, Any]) -> list[str]:
    fields = {
        "profile_id": "id",
        "model": "model_id",
        "requested_effort": "requested_effort",
        "api_effort": "api_effort",
        "thinking": "thinking",
    }
    return [
        f"prompt_profile:mismatched_{prompt_field}"
        for prompt_field, profile_field in fields.items()
        if prompt.get(prompt_field) != profile.get(profile_field)
    ]


def _profile_by_id(payload: Mapping[str, Any], *, label: str) -> dict[str, dict[str, Any]]:
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise CrossFunctionalMatrixClassificationError(f"{label} profiles must be a non-empty array")
    profiles: dict[str, dict[str, Any]] = {}
    for raw_profile in cast(list[object], raw_profiles):
        if not isinstance(raw_profile, dict):
            raise CrossFunctionalMatrixClassificationError(f"{label} profiles must contain only objects")
        profile = cast(dict[str, Any], raw_profile)
        profile_id = profile.get("id")
        if not _non_empty_string(profile_id) or profile_id in profiles:
            raise CrossFunctionalMatrixClassificationError(f"{label} has an invalid or duplicate profile id")
        profiles[cast(str, profile_id)] = profile
    return profiles


def _suite_tasks(suite: Mapping[str, Any]) -> list[dict[str, Any]]:
    if suite.get("suite_id") != "cross-functional-40-v1":
        raise CrossFunctionalMatrixClassificationError("suite identity is not cross-functional-40-v1")
    raw_tasks = suite.get("tasks")
    if not isinstance(raw_tasks, list):
        raise CrossFunctionalMatrixClassificationError("Cross-Functional 40 suite must contain exactly 40 tasks")
    task_items = cast(list[object], raw_tasks)
    if len(task_items) != 40:
        raise CrossFunctionalMatrixClassificationError("Cross-Functional 40 suite must contain exactly 40 tasks")
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_task in task_items:
        if not isinstance(raw_task, dict):
            raise CrossFunctionalMatrixClassificationError("suite tasks must contain only objects")
        task = cast(dict[str, Any], raw_task)
        task_id = task.get("id")
        prompt = task.get("prompt")
        twins = task.get("twins")
        if (
            not _non_empty_string(task_id)
            or task_id in seen
            or not isinstance(prompt, str)
            or not isinstance(twins, list)
            or not cast(list[object], twins)
            or not all(_non_empty_string(item) for item in cast(list[object], twins))
        ):
            raise CrossFunctionalMatrixClassificationError("suite contains an invalid task contract")
        seen.add(cast(str, task_id))
        tasks.append(task)
    return tasks


def _validate_calibration(
    calibration: Mapping[str, Any],
    *,
    tasks: Sequence[Mapping[str, Any]],
    path: Path,
) -> dict[str, Any]:
    if calibration.get("protocol") != HISTORICAL_CALIBRATION_PROTOCOL:
        raise CrossFunctionalMatrixClassificationError("historical calibration protocol is unsupported")
    if calibration.get("calibration_only") is not True:
        raise CrossFunctionalMatrixClassificationError("historical verdicts must be marked calibration_only")
    profile = calibration.get("profile")
    if not isinstance(profile, dict):
        raise CrossFunctionalMatrixClassificationError("historical calibration profile is missing")
    typed_profile = cast(dict[str, Any], profile)
    expected_profile = {
        "model_id": "claude-fable-5",
        "provider": "anthropic",
        "requested_effort": "high",
    }
    if any(typed_profile.get(key) != value for key, value in expected_profile.items()):
        raise CrossFunctionalMatrixClassificationError("historical calibration is not the Fable 5 High audit")
    verdicts = calibration.get("verdicts")
    if not isinstance(verdicts, dict):
        raise CrossFunctionalMatrixClassificationError("historical calibration verdicts are missing")
    typed_verdicts = cast(dict[str, Any], verdicts)
    task_ids = {cast(str, task["id"]) for task in tasks}
    if set(typed_verdicts) != task_ids:
        raise CrossFunctionalMatrixClassificationError("historical calibration does not cover the exact suite")
    passed = 0
    for task_id, raw_verdict in typed_verdicts.items():
        if not isinstance(raw_verdict, dict):
            raise CrossFunctionalMatrixClassificationError(f"historical calibration verdict {task_id!r} is malformed")
        verdict = cast(dict[str, Any], raw_verdict)
        if not isinstance(verdict.get("passed"), bool) or not _non_empty_string(verdict.get("reason")):
            raise CrossFunctionalMatrixClassificationError(f"historical calibration verdict {task_id!r} is malformed")
        passed += verdict["passed"] is True
    return {
        "calibration_id": calibration.get("calibration_id"),
        "artifact": str(path.resolve()),
        "artifact_sha256": _sha256_path(path),
        "source_artifact_sha256": calibration.get("source_artifact_sha256"),
        "profile": dict(typed_profile),
        "task_count": len(typed_verdicts),
        "historical_passes": passed,
        "historical_failures": len(typed_verdicts) - passed,
        "method": calibration.get("method"),
        "usage": "historical_calibration_only",
        "applied_to_current_attempts": False,
        "application_count": 0,
    }


def _invocation_effort_issues(invocation: Mapping[str, Any], profile: Mapping[str, Any]) -> list[str]:
    raw_config = invocation.get("config")
    if not isinstance(raw_config, dict):
        return ["invocation:missing_config"]
    config = cast(dict[str, Any], raw_config)
    issues: list[str] = []
    if config.get("model") != profile.get("model_id"):
        issues.append("invocation_config:mismatched_model")
    if config.get("provider") != profile.get("provider"):
        issues.append("invocation_config:mismatched_provider")
    provider = profile.get("provider")
    expected_effort = profile.get("api_effort")
    if provider == "openai":
        reasoning = config.get("reasoning")
        typed_reasoning = cast(dict[str, Any], reasoning) if isinstance(reasoning, dict) else None
        if typed_reasoning is None or typed_reasoning.get("effort") != expected_effort:
            issues.append("invocation_config:mismatched_effort")
    elif config.get("effort") != expected_effort:
        issues.append("invocation_config:mismatched_effort")
    if provider == "google" and config.get("thinking") != profile.get("thinking"):
        issues.append("invocation_config:mismatched_thinking")
    if provider == "anthropic":
        thinking = config.get("thinking")
        typed_thinking = cast(dict[str, Any], thinking) if isinstance(thinking, dict) else None
        thinking_type = typed_thinking.get("type") if typed_thinking is not None else None
        if thinking_type != profile.get("thinking"):
            issues.append("invocation_config:mismatched_thinking")
    return issues


def _validate_trace_artifacts(
    *,
    attempt: Mapping[str, Any],
    invocation: Mapping[str, Any],
    provider_trace: Mapping[str, Any],
    docs_trace: Mapping[str, Any],
    tool_steps: Mapping[str, Any],
    raw_diff: Mapping[str, Any],
) -> list[str]:
    issues: list[str] = []
    protocol_expectations = (
        (provider_trace, _PROVIDER_TRACE_PROTOCOL, "provider_trace"),
        (docs_trace, _DOCS_TRACE_PROTOCOL, "official_docs_trace"),
        (tool_steps, _TOOL_STEPS_PROTOCOL, "tool_steps"),
        (raw_diff, _RAW_DIFF_PROTOCOL, "raw_state_diff"),
    )
    for artifact, protocol, name in protocol_expectations:
        if artifact.get("protocol") != protocol:
            issues.append(f"{name}:mismatched_protocol")
    provider_events = provider_trace.get("events")
    docs_events = docs_trace.get("events")
    steps = tool_steps.get("steps")
    deltas = raw_diff.get("deltas")
    if not isinstance(provider_events, list):
        issues.append("provider_trace:events_not_array")
    if not isinstance(docs_events, list):
        issues.append("official_docs_trace:events_not_array")
    if not isinstance(steps, list):
        issues.append("tool_steps:steps_not_array")
    if not isinstance(deltas, list):
        issues.append("raw_state_diff:deltas_not_array")
    if (
        not isinstance(provider_events, list)
        or not isinstance(docs_events, list)
        or not isinstance(steps, list)
        or not isinstance(deltas, list)
    ):
        return issues
    typed_steps = cast(list[object], steps)
    step_kinds = [cast(dict[str, Any], item).get("kind") for item in typed_steps if isinstance(item, dict)]
    provider_step_count = sum(kind == "provider_api" for kind in step_kinds)
    docs_step_count = sum(kind == "provider_docs" for kind in step_kinds)
    declared = {
        "tool_calls": len(typed_steps),
        "provider_tool_calls": provider_step_count,
        "official_docs_tool_calls": docs_step_count,
        "raw_state_delta_count": len(cast(list[object], deltas)),
    }
    for field, expected in declared.items():
        if attempt.get(field) != expected:
            issues.append(f"attempt:{field}_contradicts_artifact")
    if len(cast(list[object], provider_events)) != provider_step_count:
        issues.append("provider_trace:count_contradicts_tool_steps")
    if len(cast(list[object], docs_events)) != docs_step_count:
        issues.append("official_docs_trace:count_contradicts_tool_steps")
    invocation_calls = invocation.get("tool_calls")
    if (
        isinstance(invocation_calls, bool)
        or not isinstance(invocation_calls, int)
        or invocation_calls < len(typed_steps)
    ):
        issues.append("invocation:tool_call_count_contradicts_trace")
    return issues


def _snapshot_issues(snapshot: Mapping[str, Any], *, task: Mapping[str, Any], name: str) -> list[str]:
    providers = snapshot.get("providers")
    if not isinstance(providers, dict):
        return [f"{name}:providers_not_object"]
    typed_providers = cast(dict[str, Any], providers)
    expected = set(cast(list[str], task["twins"]))
    if set(typed_providers) != expected:
        return [f"{name}:provider_set_mismatch"]
    return []


def _snapshot_evidence_gaps(
    baseline: Mapping[str, Any],
    final: Mapping[str, Any],
) -> list[str]:
    baseline_queries = baseline.get("queries")
    final_queries = final.get("queries")
    if not isinstance(baseline_queries, dict) or not isinstance(final_queries, dict):
        return ["snapshot_query_contract_missing_or_malformed"]
    typed_baseline_queries = cast(dict[str, Any], baseline_queries)
    typed_final_queries = cast(dict[str, Any], final_queries)
    if not typed_baseline_queries or not typed_final_queries:
        return ["missing_snapshot_query_evidence"]
    if set(typed_baseline_queries) != set(typed_final_queries):
        return ["snapshot_query_contract_mismatch"]
    return ["semantic_grade_not_applied_by_integrity_classifier"]


def _archived_old_gateway_ceiling_retry_is_safe(
    *,
    archive_dir: Path,
    task_id: str,
    profile_id: str,
    attempt: Mapping[str, Any],
    invocation: Mapping[str, Any],
) -> bool:
    issues: list[str] = []
    control = _read_artifact(archive_dir / "control.json", name="control.json", issues=issues)
    prompt = _read_artifact(archive_dir / "prompt.json", name="prompt.json", issues=issues)
    baseline = _read_artifact(
        archive_dir / "baseline-state.json",
        name="baseline-state.json",
        issues=issues,
    )
    final = _read_artifact(
        archive_dir / "final-state.json",
        name="final-state.json",
        issues=issues,
    )
    raw_diff = _read_artifact(
        archive_dir / "raw-state-diff.json",
        name="raw-state-diff.json",
        issues=issues,
    )
    provider_trace = _read_artifact(
        archive_dir / "provider-trace.json",
        name="provider-trace.json",
        issues=issues,
    )
    docs_trace = _read_artifact(
        archive_dir / "official-docs-trace.json",
        name="official-docs-trace.json",
        issues=issues,
    )
    tool_steps = _read_artifact(
        archive_dir / "tool-steps.json",
        name="tool-steps.json",
        issues=issues,
    )
    if issues or any(
        artifact is None
        for artifact in (
            control,
            prompt,
            baseline,
            final,
            raw_diff,
            provider_trace,
            docs_trace,
            tool_steps,
        )
    ):
        return False
    assert control is not None
    assert prompt is not None
    assert baseline is not None
    assert final is not None
    assert raw_diff is not None
    assert provider_trace is not None
    assert docs_trace is not None
    assert tool_steps is not None

    run_id = attempt.get("run_id")
    model = attempt.get("model")
    response_model = attempt.get("response_model")
    provider = attempt.get("provider")
    scenario_id = attempt.get("scenario_id")
    if (
        not _non_empty_string(run_id)
        or not _non_empty_string(model)
        or not _non_empty_string(response_model)
        or not _non_empty_string(provider)
        or not _non_empty_string(scenario_id)
        or attempt.get("cleanup_succeeded") is not True
        or control.get("protocol") != _CONTROL_PROTOCOL
        or control.get("instance_id") != task_id
        or control.get("run_id") != run_id
        or control.get("scenario_id") != scenario_id
        or prompt.get("profile_id") != profile_id
        or prompt.get("model") != model
        or prompt.get("user_prompt") != attempt.get("prompt")
        or invocation.get("requested_model") != model
        or invocation.get("response_model") != response_model
        or invocation.get("provider") != provider
        or invocation.get("user_prompt") != prompt.get("user_prompt")
        or invocation.get("system_prompt") != prompt.get("system_prompt")
        or invocation.get("stop_reason") != attempt.get("stop_reason")
        or invocation.get("final_text") != attempt.get("final_text")
        or not isinstance(baseline.get("providers"), dict)
        or not isinstance(baseline.get("queries"), dict)
        or not isinstance(final.get("providers"), dict)
        or not isinstance(final.get("queries"), dict)
    ):
        return False
    if _validate_trace_artifacts(
        attempt=attempt,
        invocation=invocation,
        provider_trace=provider_trace,
        docs_trace=docs_trace,
        tool_steps=tool_steps,
        raw_diff=raw_diff,
    ):
        return False
    return bool(
        legacy_gateway_ceiling_rejections(
            prompt=prompt,
            invocation=invocation,
            provider_trace=provider_trace,
            docs_trace=docs_trace,
            tool_steps=tool_steps,
        )
    )


def _retry_archive_proves_safe_retries(
    *,
    profile_dir: Path,
    task_id: str,
    profile_id: str,
    attempt_number: object,
) -> bool:
    retry_root = profile_dir / "retry-archive" / task_id
    if not retry_root.exists():
        return True
    if not retry_root.is_dir():
        return False
    entries = list(retry_root.iterdir())
    if not entries:
        return True
    if not isinstance(attempt_number, int) or isinstance(attempt_number, bool) or attempt_number <= 1:
        return False

    archives: dict[int, Path] = {}
    for entry in entries:
        match = re.fullmatch(r"attempt-(\d{4})", entry.name)
        if not entry.is_dir() or match is None:
            return False
        archive_number = int(match.group(1))
        if archive_number in archives:
            return False
        archives[archive_number] = entry
    if set(archives) != set(range(1, attempt_number)):
        return False

    for archive_number, archive_dir in archives.items():
        metadata_issues: list[str] = []
        metadata = _read_artifact(
            archive_dir / "archive-metadata.json",
            name="archive-metadata.json",
            issues=metadata_issues,
        )
        if metadata_issues or metadata is None:
            return False
        if (
            metadata.get("protocol") != _RETRY_ARCHIVE_PROTOCOL
            or metadata.get("archive_number") != archive_number
            or metadata.get("profile_id") != profile_id
            or metadata.get("task_id") != task_id
        ):
            return False
        reason = metadata.get("archive_reason")
        archived_attempt_path = archive_dir / "attempt.json"
        if reason == "interrupted_before_attempt":
            if archived_attempt_path.exists() or any(archive_dir.rglob("invocation.json")):
                return False
            if not _archived_cleanup_is_safe(archive_dir=archive_dir, cleanup=metadata.get("cleanup")):
                return False
            continue
        if reason == "explicit_interrupted_infrastructure_retry":
            if any(archive_dir.rglob("invocation.json")) or not _archived_cleanup_is_safe(
                archive_dir=archive_dir,
                cleanup=metadata.get("cleanup"),
            ):
                return False
            if not archived_attempt_path.exists():
                continue
        elif reason not in {
            "zero_invocation_infrastructure_invalid",
            "explicit_model_infrastructure_retry",
            "explicit_post_invocation_infrastructure_retry",
            "explicit_model_terminal_retry",
            "explicit_missing_snapshot_evidence_retry",
            "explicit_old_gateway_ceiling_retry",
        }:
            return False
        archived_issues: list[str] = []
        archived_attempt = _read_artifact(
            archived_attempt_path,
            name="attempt.json",
            issues=archived_issues,
        )
        if archived_issues or archived_attempt is None:
            return False
        if (
            archived_attempt.get("protocol") != _ATTEMPT_PROTOCOL
            or archived_attempt.get("profile_id") != profile_id
            or archived_attempt.get("task_id") != task_id
        ):
            return False
        if reason in {
            "explicit_model_infrastructure_retry",
            "explicit_post_invocation_infrastructure_retry",
            "explicit_model_terminal_retry",
            "explicit_missing_snapshot_evidence_retry",
            "explicit_old_gateway_ceiling_retry",
        }:
            if not _archived_cleanup_is_safe(
                archive_dir=archive_dir,
                cleanup=metadata.get("cleanup"),
            ):
                return False
            invocation_issues: list[str] = []
            archived_invocation = _read_artifact(
                archive_dir / "invocation.json",
                name="invocation.json",
                issues=invocation_issues,
            )
            model_status = archived_attempt.get("model_status")
            allowed_statuses = (
                {"api_error", "invalid_response"}
                if reason == "explicit_model_infrastructure_retry"
                else {"completed"}
                if reason == "explicit_post_invocation_infrastructure_retry"
                else {"completed"}
                if reason == "explicit_missing_snapshot_evidence_retry"
                else {"completed"}
                if reason == "explicit_old_gateway_ceiling_retry"
                else _MODEL_TERMINAL_STATUSES
            )
            allowed_attempt_statuses = (
                {"infrastructure_invalid"}
                if reason == "explicit_post_invocation_infrastructure_retry"
                else {"candidate_complete"}
                if reason == "explicit_missing_snapshot_evidence_retry"
                else {"candidate_complete"}
                if reason == "explicit_old_gateway_ceiling_retry"
                else {"infrastructure_invalid", "candidate_complete"}
            )
            if (
                invocation_issues
                or archived_invocation is None
                or model_status not in allowed_statuses
                or archived_invocation.get("status") != model_status
                or archived_attempt.get("attempt_status") not in allowed_attempt_statuses
            ):
                return False
            if reason == "explicit_missing_snapshot_evidence_retry":
                snapshot_issues: list[str] = []
                baseline = _read_artifact(
                    archive_dir / "baseline-state.json",
                    name="baseline-state.json",
                    issues=snapshot_issues,
                )
                final = _read_artifact(
                    archive_dir / "final-state.json",
                    name="final-state.json",
                    issues=snapshot_issues,
                )
                if (
                    snapshot_issues
                    or baseline is None
                    or final is None
                    or baseline.get("queries") != {}
                    or final.get("queries") != {}
                ):
                    return False
            if reason == "explicit_old_gateway_ceiling_retry" and not (
                _archived_old_gateway_ceiling_retry_is_safe(
                    archive_dir=archive_dir,
                    task_id=task_id,
                    profile_id=profile_id,
                    attempt=archived_attempt,
                    invocation=archived_invocation,
                )
            ):
                return False
            continue
        if archived_attempt.get("attempt_status") != "infrastructure_invalid":
            return False
        if archived_attempt.get("model_status") is not None or archived_attempt.get("final_text") not in (None, ""):
            return False
        if not _archived_cleanup_is_safe(archive_dir=archive_dir, cleanup=metadata.get("cleanup")):
            return False
        if reason == "zero_invocation_infrastructure_invalid":
            if any(archive_dir.rglob("invocation.json")):
                return False
            for field in ("tool_calls", "provider_tool_calls", "official_docs_tool_calls"):
                if archived_attempt.get(field) != 0:
                    return False
    return True


def _archived_cleanup_is_safe(*, archive_dir: Path, cleanup: object) -> bool:
    run_ids: set[str] = set()
    for filename in ("attempt.json", "control.json"):
        path = archive_dir / filename
        if not path.exists():
            continue
        issues: list[str] = []
        payload = _read_artifact(path, name=filename, issues=issues)
        if issues or payload is None:
            return False
        run_id = payload.get("run_id")
        if run_id is not None:
            if not _non_empty_string(run_id):
                return False
            run_ids.add(cast(str, run_id))
    if len(run_ids) > 1:
        return False
    if not run_ids:
        return cleanup == {"outcome": "interrupted_before_control_persisted"}
    return isinstance(cleanup, dict) and cleanup_payload_proves_inert(
        cleanup,
        expected_run_id=next(iter(run_ids)),
    )


def _legacy_attempt_number_is_safe(
    *,
    profile_dir: Path,
    task_dir: Path,
    task_id: str,
    profile: Mapping[str, Any],
    attempt: Mapping[str, Any],
    control: Mapping[str, Any] | None,
    cleanup: Mapping[str, Any] | None,
    invocation: Mapping[str, Any] | None,
) -> bool:
    attempt_number = attempt.get("attempt_number")
    if isinstance(attempt_number, int) and not isinstance(attempt_number, bool) and attempt_number == 1:
        return True
    legacy_missing_number = "attempt_number" not in attempt
    legacy_retry_number = (
        isinstance(attempt_number, int) and not isinstance(attempt_number, bool) and attempt_number > 1
    )
    if not legacy_missing_number and not legacy_retry_number:
        return False
    run_id = attempt.get("run_id")
    if (
        attempt.get("task_id") != task_id
        or _attempt_profile_issues(attempt, profile)
        or not _non_empty_string(run_id)
        or control is None
        or control.get("run_id") != run_id
        or cleanup is None
        or not cleanup_payload_proves_inert(cleanup, expected_run_id=cast(str, run_id))
        or invocation is None
        or invocation.get("status") not in _MODEL_TERMINAL_STATUSES | {"completed"}
        or attempt.get("model_status") != invocation.get("status")
        or attempt.get("attempt_status") != "candidate_complete"
        or invocation.get("requested_model") != profile.get("model_id")
        or invocation.get("provider") != profile.get("provider")
        or invocation.get("response_model") != profile.get("model_id")
    ):
        return False
    invocation_artifacts = {path.name for path in task_dir.glob("*invocation*.json") if path.is_file()}
    if not invocation_artifacts <= {"invocation.json", "model-invocation-started.json"} or (
        "invocation.json" not in invocation_artifacts
    ):
        return False
    marker_path = task_dir / "model-invocation-started.json"
    if marker_path.is_file():
        marker_issues: list[str] = []
        marker = _read_artifact(marker_path, name=marker_path.name, issues=marker_issues)
        if marker_issues or marker is None:
            return False
        if (
            marker.get("protocol") != "arga-bench-model-invocation-started/1"
            or marker.get("profile_id") != profile.get("id")
            or marker.get("task_id") != task_id
            or marker.get("attempt_number") != attempt_number
        ):
            return False
    return _retry_archive_proves_safe_retries(
        profile_dir=profile_dir,
        task_id=task_id,
        profile_id=cast(str, profile["id"]),
        attempt_number=attempt_number,
    )


def _classify_task(
    *,
    matrix_dir: Path,
    profile_dir: Path,
    profile: Mapping[str, Any],
    task: Mapping[str, Any],
    scenario_ids: Mapping[str, Any] | None,
    inherited_issues: Sequence[str],
) -> dict[str, Any]:
    task_id = cast(str, task["id"])
    profile_id = cast(str, profile["id"])
    task_dir = profile_dir / "tasks" / task_id
    issues = list(inherited_issues)
    attempt = _read_artifact(task_dir / "attempt.json", name="attempt.json", issues=issues)
    control = _read_artifact(task_dir / "control.json", name="control.json", issues=issues)
    cleanup = _read_artifact(task_dir / "cleanup.json", name="cleanup.json", issues=issues)
    prompt = _read_artifact(task_dir / "prompt.json", name="prompt.json", issues=issues)
    invocation = _read_artifact(task_dir / "invocation.json", name="invocation.json", issues=issues)
    baseline = _read_artifact(task_dir / "baseline-state.json", name="baseline-state.json", issues=issues)
    final = _read_artifact(task_dir / "final-state.json", name="final-state.json", issues=issues)
    raw_diff = _read_artifact(task_dir / "raw-state-diff.json", name="raw-state-diff.json", issues=issues)
    provider_trace = _read_artifact(task_dir / "provider-trace.json", name="provider-trace.json", issues=issues)
    docs_trace = _read_artifact(
        task_dir / "official-docs-trace.json",
        name="official-docs-trace.json",
        issues=issues,
    )
    tool_steps = _read_artifact(task_dir / "tool-steps.json", name="tool-steps.json", issues=issues)

    model_status: str | None = None
    run_id: str | None = None
    if attempt is not None:
        if attempt.get("protocol") != _ATTEMPT_PROTOCOL:
            issues.append("attempt:mismatched_protocol")
        if attempt.get("task_id") != task_id:
            issues.append("attempt:mismatched_task_id")
        if not _legacy_attempt_number_is_safe(
            profile_dir=profile_dir,
            task_dir=task_dir,
            task_id=task_id,
            profile=profile,
            attempt=attempt,
            control=control,
            cleanup=cleanup,
            invocation=invocation,
        ):
            issues.append("attempt:invalid_attempt_number")
        issues.extend(_attempt_profile_issues(attempt, profile))
        if attempt.get("response_model") != profile.get("model_id"):
            issues.append("attempt:mismatched_response_model")
        if attempt.get("prompt") != task.get("prompt"):
            issues.append("attempt:mismatched_prompt")
        expected_prompt_hash = _prompt_hash(cast(str, task["prompt"]))
        if attempt.get("prompt_sha256") != expected_prompt_hash:
            issues.append("attempt:mismatched_prompt_sha256")
        if attempt.get("prompt_matches_tasks_md") is not True:
            issues.append("attempt:prompt_match_not_proven")
        raw_status = attempt.get("model_status")
        model_status = raw_status if isinstance(raw_status, str) else None
        raw_run_id = attempt.get("run_id")
        run_id = raw_run_id if _non_empty_string(raw_run_id) else None

    registered_scenario_id = scenario_ids.get(task_id) if scenario_ids is not None else None
    if not _non_empty_string(registered_scenario_id):
        issues.append("scenario_mapping:missing_task_id")
    attempt_scenario_id = attempt.get("scenario_id") if attempt is not None else None
    if attempt is not None and not _non_empty_string(attempt_scenario_id):
        issues.append("attempt:missing_scenario_id")

    if control is not None:
        if control.get("protocol") != _CONTROL_PROTOCOL:
            issues.append("control:mismatched_protocol")
        if control.get("instance_id") != task_id:
            issues.append("control:mismatched_task_id")
        if control.get("scenario_id") != attempt_scenario_id:
            issues.append("control:mismatched_scenario_id")
        if control.get("scenario_content_sha256") not in _accepted_content_hashes(task):
            issues.append("control:mismatched_scenario_content_sha256")
        if not _non_empty_string(control.get("run_id")) or control.get("run_id") != run_id:
            issues.append("control:mismatched_run_id")
        twin_run = control.get("twin_run")
        typed_twin_run = cast(dict[str, Any], twin_run) if isinstance(twin_run, dict) else None
        if typed_twin_run is None or typed_twin_run.get("status") != "ready":
            issues.append("control:ready_state_not_proven")
    if attempt is not None and attempt.get("seed_status") != "ready":
        issues.append("attempt:ready_seed_not_proven")

    if cleanup is None or run_id is None or not cleanup_payload_proves_inert(cleanup, expected_run_id=run_id):
        issues.append("cleanup:inert_twin_not_proven")
    if attempt is not None and attempt.get("cleanup_succeeded") is not True:
        issues.append("attempt:cleanup_not_proven")

    if prompt is not None:
        if prompt.get("protocol") != _PROMPT_PROTOCOL:
            issues.append("prompt:mismatched_protocol")
        issues.extend(_prompt_profile_issues(prompt, profile))
        if prompt.get("user_prompt") != task.get("prompt"):
            issues.append("prompt:mismatched_user_prompt")
        if not _non_empty_string(prompt.get("system_prompt")):
            issues.append("prompt:missing_system_prompt")

    if invocation is not None:
        if invocation.get("requested_model") != profile.get("model_id"):
            issues.append("invocation:mismatched_requested_model")
        if invocation.get("provider") != profile.get("provider"):
            issues.append("invocation:mismatched_provider")
        if invocation.get("response_model") != profile.get("model_id"):
            issues.append("invocation:mismatched_response_model")
        if invocation.get("status") != model_status:
            issues.append("invocation:mismatched_status")
        if attempt is not None and invocation.get("stop_reason") != attempt.get("stop_reason"):
            issues.append("invocation:mismatched_stop_reason")
        if attempt is not None and invocation.get("final_text") != attempt.get("final_text"):
            issues.append("invocation:mismatched_final_text")
        if prompt is not None:
            if invocation.get("user_prompt") != prompt.get("user_prompt"):
                issues.append("invocation:mismatched_user_prompt")
            if invocation.get("system_prompt") != prompt.get("system_prompt"):
                issues.append("invocation:mismatched_system_prompt")
        issues.extend(_invocation_effort_issues(invocation, profile))

    if baseline is not None:
        issues.extend(_snapshot_issues(baseline, task=task, name="baseline_state"))
    if final is not None:
        issues.extend(_snapshot_issues(final, task=task, name="final_state"))
    if all(
        artifact is not None for artifact in (attempt, invocation, provider_trace, docs_trace, tool_steps, raw_diff)
    ):
        issues.extend(
            _validate_trace_artifacts(
                attempt=cast(dict[str, Any], attempt),
                invocation=cast(dict[str, Any], invocation),
                provider_trace=cast(dict[str, Any], provider_trace),
                docs_trace=cast(dict[str, Any], docs_trace),
                tool_steps=cast(dict[str, Any], tool_steps),
                raw_diff=cast(dict[str, Any], raw_diff),
            )
        )

    output_limit_terminal = bool(
        model_status == "incomplete"
        and attempt is not None
        and invocation is not None
        and attempt.get("stop_reason") == "MAX_TOKENS"
        and invocation.get("stop_reason") == "MAX_TOKENS"
    )
    if model_status not in _KNOWN_MODEL_STATUSES:
        issues.append("attempt:missing_or_unknown_model_status")
    elif model_status not in _MODEL_TERMINAL_STATUSES and model_status != "completed" and not output_limit_terminal:
        issues.append(f"model_infrastructure_status:{model_status}")
    if attempt is not None and (model_status in _MODEL_TERMINAL_STATUSES | {"completed"} or output_limit_terminal):
        if attempt.get("attempt_status") != "candidate_complete":
            issues.append("attempt:terminal_status_not_preserved")

    issues = sorted(set(issues))
    if issues:
        execution_class: ExecutionClass = "infrastructure_invalid"
        terminal_reason: str | None = None
        validity: TrialValidity = "invalid_infrastructure"
        evidence_status = "not_evaluated"
        evidence_gaps: list[str] = []
    else:
        execution_class = "exact_completed" if model_status == "completed" else "model_terminal"
        terminal_reason = (
            "output_limit_exceeded"
            if output_limit_terminal
            else model_status
            if model_status in _MODEL_TERMINAL_STATUSES
            else None
        )
        assert baseline is not None and final is not None
        evidence_gaps = _snapshot_evidence_gaps(baseline, final)
        validity = "invalid_grader"
        evidence_status = "evidence_gap"

    return {
        "trial_id": f"{profile_id}/{task_id}",
        "profile_id": profile_id,
        "task_id": task_id,
        "model_id": profile["model_id"],
        "requested_effort": profile["requested_effort"],
        "api_effort": profile["api_effort"],
        "execution_class": execution_class,
        "model_terminal_reason": terminal_reason,
        "model_status": model_status,
        "validity": validity,
        "state_grade_complete": False,
        "semantic_outcome": None,
        "score_eligible": False,
        "evidence_status": evidence_status,
        "evidence_gaps": evidence_gaps,
        "integrity": {
            "passed": not issues,
            "issues": issues,
            "artifact_sha256": _artifact_hashes(task_dir),
        },
        "run_id": run_id,
        "artifacts": {
            name.removesuffix(".json").replace("-", "_"): str((task_dir / name).relative_to(matrix_dir))
            for name in _TASK_ARTIFACTS
            if (task_dir / name).is_file()
        },
        "historical_calibration_applied": False,
    }


def _counter_payload(attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    execution = Counter(str(item["execution_class"]) for item in attempts)
    terminal_reasons = Counter(
        cast(str, item["model_terminal_reason"])
        for item in attempts
        if isinstance(item.get("model_terminal_reason"), str)
    )
    validity = Counter(str(item["validity"]) for item in attempts)
    evidence_gaps = Counter(str(gap) for item in attempts for gap in cast(list[object], item.get("evidence_gaps", [])))
    outcomes = Counter(
        cast(str, item["semantic_outcome"])
        for item in attempts
        if item.get("validity") == "valid"
        and item.get("score_eligible") is True
        and isinstance(item.get("semantic_outcome"), str)
    )
    score_denominator = sum(outcomes.values())
    completed_or_terminal = execution["exact_completed"] + execution["model_terminal"]
    return {
        "scheduled_attempts": len(attempts),
        "execution": {
            "exact_completed": execution["exact_completed"],
            "model_terminal": execution["model_terminal"],
            "model_terminal_by_reason": dict(sorted(terminal_reasons.items())),
            "infrastructure_invalid": execution["infrastructure_invalid"],
            "execution_completion_denominator": completed_or_terminal,
            "exact_completion_rate": (
                execution["exact_completed"] / completed_or_terminal if completed_or_terminal else None
            ),
        },
        "validity": {
            "valid": validity["valid"],
            "invalid_infrastructure": validity["invalid_infrastructure"],
            "invalid_grader": validity["invalid_grader"],
        },
        "evidence_gaps": dict(sorted(evidence_gaps.items())),
        "scoring": {
            "denominator": score_denominator,
            "passed": outcomes["passed"],
            "failed": outcomes["failed"],
            "unsafe": outcomes["unsafe"],
            "pass_rate": outcomes["passed"] / score_denominator if score_denominator else None,
            "excluded_invalid_infrastructure": validity["invalid_infrastructure"],
            "excluded_invalid_grader": validity["invalid_grader"],
        },
    }


def _matrix_config_issues(
    config: Mapping[str, Any] | None,
    *,
    suite_id: str,
    profiles: Mapping[str, Mapping[str, Any]],
    task_count: int,
) -> tuple[list[str], Mapping[str, Mapping[str, Any]]]:
    if config is None:
        return ["matrix_config:missing_or_unreadable"], {}
    issues: list[str] = []
    if config.get("protocol") != _MATRIX_CONFIG_PROTOCOL:
        issues.append("matrix_config:mismatched_protocol")
    if config.get("suite_id") != suite_id:
        issues.append("matrix_config:mismatched_suite_id")
    if config.get("profile_count") != len(profiles):
        issues.append("matrix_config:mismatched_profile_count")
    if config.get("scenarios_per_profile") != task_count:
        issues.append("matrix_config:mismatched_scenarios_per_profile")
    if config.get("total_trials") != len(profiles) * task_count:
        issues.append("matrix_config:mismatched_total_trials")
    if config.get("attempts_per_model_scenario_pair") != 1:
        issues.append("matrix_config:mismatched_attempts_per_pair")
    try:
        configured = _profile_by_id(config, label="matrix config")
    except CrossFunctionalMatrixClassificationError:
        return sorted({*issues, "matrix_config:invalid_profiles"}), {}
    if set(configured) != set(profiles):
        issues.append("matrix_config:profile_set_mismatch")
    return sorted(set(issues)), configured


def _profile_inputs(
    profile_dir: Path,
    *,
    suite_id: str,
    task_ids: set[str],
    expected_profile: Mapping[str, Any],
    configured_profile: Mapping[str, Any] | None,
    matrix_issues: Sequence[str],
) -> tuple[list[str], Mapping[str, Any] | None]:
    issues = list(matrix_issues)
    issues.extend(_profile_identity_issues(configured_profile, expected_profile, prefix="matrix_config_profile"))
    artifact_issues: list[str] = []
    run_config = _read_artifact(profile_dir / "run-config.json", name="run-config.json", issues=artifact_issues)
    scenarios = _read_artifact(
        profile_dir / "staging-scenarios.json",
        name="staging-scenarios.json",
        issues=artifact_issues,
    )
    issues.extend(f"profile_{item}" for item in artifact_issues)
    if run_config is not None:
        if run_config.get("protocol") != _PROFILE_CONFIG_PROTOCOL:
            issues.append("run_config:mismatched_protocol")
        if run_config.get("suite_id") != suite_id:
            issues.append("run_config:mismatched_suite_id")
        if run_config.get("attempts_per_scenario") != 1:
            issues.append("run_config:mismatched_attempts_per_scenario")
        raw_profile = run_config.get("profile")
        run_profile = cast(dict[str, Any], raw_profile) if isinstance(raw_profile, dict) else None
        issues.extend(_profile_identity_issues(run_profile, expected_profile, prefix="run_config_profile"))
    scenario_ids: Mapping[str, Any] | None = None
    if scenarios is not None:
        if scenarios.get("suite_tag") != "suite:cross-functional-40-v1":
            issues.append("staging_scenarios:mismatched_suite_tag")
        raw_ids = scenarios.get("scenario_ids")
        typed_ids = cast(dict[str, Any], raw_ids) if isinstance(raw_ids, dict) else None
        if typed_ids is None or set(typed_ids) != task_ids:
            issues.append("staging_scenarios:task_set_mismatch")
        else:
            scenario_ids = typed_ids
            if not all(_non_empty_string(value) for value in scenario_ids.values()):
                issues.append("staging_scenarios:invalid_scenario_id")
    return sorted(set(issues)), scenario_ids


def _mark_duplicate_run_ids(attempts: list[dict[str, Any]]) -> None:
    by_run_id: dict[str, list[dict[str, Any]]] = {}
    for attempt in attempts:
        run_id = attempt.get("run_id")
        if isinstance(run_id, str):
            by_run_id.setdefault(run_id, []).append(attempt)
    for duplicates in by_run_id.values():
        if len(duplicates) < 2:
            continue
        for attempt in duplicates:
            integrity = cast(dict[str, Any], attempt["integrity"])
            raw_issues = integrity.get("issues")
            issues = cast(list[str], raw_issues) if isinstance(raw_issues, list) else []
            issues.append("matrix:duplicate_run_id")
            integrity["issues"] = sorted(set(issues))
            integrity["passed"] = False
            attempt["execution_class"] = "infrastructure_invalid"
            attempt["model_terminal_reason"] = None
            attempt["validity"] = "invalid_infrastructure"
            attempt["evidence_status"] = "not_evaluated"
            attempt["evidence_gaps"] = []


def classify_cross_functional_matrix(
    matrix_dir: Path,
    *,
    suite_path: Path,
    model_matrix_path: Path,
    historical_calibration_path: Path,
) -> dict[str, Any]:
    """Classify a preserved Cross-Functional 40 matrix without grading semantics.

    This function deliberately never infers a semantic pass from raw admin-state
    diffs, traces, model output, or the historical Fable 5 High verdicts.
    """

    matrix_dir = matrix_dir.resolve()
    suite_path = suite_path.resolve()
    model_matrix_path = model_matrix_path.resolve()
    historical_calibration_path = historical_calibration_path.resolve()
    suite = _load_trusted_object(suite_path, label="Cross-Functional 40 suite")
    tasks = _suite_tasks(suite)
    model_matrix = _load_trusted_object(model_matrix_path, label="model matrix")
    profiles = _profile_by_id(model_matrix, label="model matrix")
    if len(profiles) != 31:
        raise CrossFunctionalMatrixClassificationError("Cross-Functional model matrix must contain 31 profiles")
    calibration_payload = _load_trusted_object(
        historical_calibration_path,
        label="historical Fable 5 High calibration",
    )
    calibration = _validate_calibration(
        calibration_payload,
        tasks=tasks,
        path=historical_calibration_path,
    )

    matrix_config_issues: list[str] = []
    matrix_config = _read_artifact(
        matrix_dir / "matrix-config.json",
        name="matrix-config.json",
        issues=matrix_config_issues,
    )
    root_issues, configured_profiles = _matrix_config_issues(
        matrix_config,
        suite_id=cast(str, suite["suite_id"]),
        profiles=profiles,
        task_count=len(tasks),
    )
    root_issues = sorted(set([*matrix_config_issues, *root_issues]))

    attempts: list[dict[str, Any]] = []
    task_ids = {cast(str, task["id"]) for task in tasks}
    for profile_id, profile in profiles.items():
        profile_dir = matrix_dir / "profiles" / profile_id
        profile_issues, scenario_ids = _profile_inputs(
            profile_dir,
            suite_id=cast(str, suite["suite_id"]),
            task_ids=task_ids,
            expected_profile=profile,
            configured_profile=configured_profiles.get(profile_id),
            matrix_issues=root_issues,
        )
        for task in tasks:
            attempts.append(
                _classify_task(
                    matrix_dir=matrix_dir,
                    profile_dir=profile_dir,
                    profile=profile,
                    task=task,
                    scenario_ids=scenario_ids,
                    inherited_issues=profile_issues,
                )
            )
    _mark_duplicate_run_ids(attempts)

    by_profile: dict[str, dict[str, Any]] = {}
    for profile_id, profile in profiles.items():
        profile_attempts = [item for item in attempts if item["profile_id"] == profile_id]
        by_profile[profile_id] = {
            "profile": {field: profile[field] for field in _PROFILE_IDENTITY_FIELDS},
            **_counter_payload(profile_attempts),
        }

    matching_calibration_profiles = [
        profile_id
        for profile_id, profile in profiles.items()
        if profile.get("model_id") == "claude-fable-5" and profile.get("requested_effort") == "high"
    ]
    calibration["matching_current_profile_ids"] = matching_calibration_profiles
    return {
        "protocol": CROSS_FUNCTIONAL_MATRIX_CLASSIFICATION_PROTOCOL,
        "suite_id": suite["suite_id"],
        "source_matrix_dir": str(matrix_dir),
        "source_sha256": {
            "suite": _sha256_path(suite_path),
            "model_matrix": _sha256_path(model_matrix_path),
            "matrix_config": (
                _sha256_bytes((matrix_dir / "matrix-config.json").read_bytes())
                if (matrix_dir / "matrix-config.json").is_file()
                else None
            ),
        },
        "classification_policy": {
            "semantic_pass_inference": "disabled",
            "exact_completed_status": "completed",
            "model_terminal_statuses": sorted(_MODEL_TERMINAL_STATUSES),
            "other_or_missing_model_status": "infrastructure_invalid",
            "empty_snapshot_queries": "invalid_grader/evidence_gap",
            "score_denominator": "valid semantic outcomes only",
            "invalid_infrastructure_excluded": True,
            "invalid_grader_excluded": True,
            "historical_verdict_application": "disabled",
        },
        "matrix_integrity_issues": root_issues,
        "historical_calibration": calibration,
        "totals": _counter_payload(attempts),
        "profiles": by_profile,
        "attempts": attempts,
        "remaining_semantic_grading_gap": {
            "status": "implemented_downstream",
            "grader": "cross_functional_fair_v1",
            "required": (
                "Apply the per-task fair semantic grader after this artifact-integrity classification."
            ),
            "raw_state_diff_is_authoritative": False,
            "provider_trace_is_authoritative_for_business_outcomes": False,
        },
    }


def write_cross_functional_matrix_report(
    report: dict[str, Any],
    output_path: Path,
    *,
    source_matrix_dir: Path,
) -> None:
    """Write a report outside the preserved matrix tree."""

    source = source_matrix_dir.resolve()
    output = output_path.resolve()
    if output == source or output.is_relative_to(source):
        raise CrossFunctionalMatrixClassificationError(
            "refusing to write the offline report inside the preserved matrix run"
        )
    write_private_json(output, report)


__all__ = [
    "CROSS_FUNCTIONAL_MATRIX_CLASSIFICATION_PROTOCOL",
    "CrossFunctionalMatrixClassificationError",
    "classify_cross_functional_matrix",
    "write_cross_functional_matrix_report",
]
