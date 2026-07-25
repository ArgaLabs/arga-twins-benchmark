from __future__ import annotations

import asyncio
import errno
import fcntl
import json
import os
import re
import socket
import subprocess
import sys
import traceback
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
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
from arga_twins_benchmark.lifecycle import (
    cleanup_instance,
    cleanup_payload_proves_inert,
    cleanup_twin_run,
    provision_instance,
    write_private_json,
)
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

PROVISION_TIMEOUT_SECONDS = 1_200
ORPHAN_TWIN_LEASE_GRACE_SECONDS = 300


class SuiteRunLockedError(RuntimeError):
    """Raised when another process already owns a suite's execution lock."""


class DirtyRunnerTreeError(RuntimeError):
    """Raised when benchmark results cannot be attributed to a clean revision."""


class _SuiteRunLock:
    """A non-blocking, cross-process lock that remains held for a matrix run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: Any | None = None

    def __enter__(self) -> _SuiteRunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags, 0o600)
        os.fchmod(fd, 0o600)
        lock_file = os.fdopen(fd, "r+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EAGAIN}:
                lock_file.close()
                raise
            owner = _read_lock_owner(lock_file)
            lock_file.close()
            owner_detail = f"; owner={json.dumps(owner, sort_keys=True)}" if owner else ""
            raise SuiteRunLockedError(
                f"suite run is already active for {self.path.parent.name!r}{owner_detail}"
            ) from error

        try:
            lock_file.seek(0)
            lock_file.truncate()
            json.dump(
                {
                    "protocol": "arga-bench-suite-lock/1",
                    "pid": os.getpid(),
                    "hostname": socket.gethostname(),
                    "acquired_at": _utc_now(),
                },
                lock_file,
                sort_keys=True,
            )
            lock_file.write("\n")
            lock_file.flush()
            os.fsync(lock_file.fileno())
        except BaseException:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()
            raise
        self._file = lock_file
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        lock_file = self._file
        self._file = None
        if lock_file is None:
            return
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


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
        return {str(key): _jsonable(item) for key, item in cast(dict[object, object], value).items()}
    return value


def _write_provider_trace(path: Path, gateway: ProviderGateway) -> None:
    write_private_json(
        path,
        {
            "protocol": "arga-bench-provider-trace/1",
            "events": cast(list[object], _jsonable(gateway.trace_records)),
        },
    )


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _read_lock_owner(lock_file: Any) -> dict[str, object] | None:
    try:
        lock_file.seek(0)
        payload: object = json.loads(lock_file.read(16_384))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return cast(dict[str, object], payload) if isinstance(payload, dict) else None


def _runner_commit() -> str:
    source_repository = Path(__file__).resolve().parents[3]
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(source_repository), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        status = subprocess.check_output(
            [
                "git",
                "-C",
                str(source_repository),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise DirtyRunnerTreeError(
            f"benchmark source repository is unavailable at {source_repository}; "
            "run from an editable Git checkout with a committed revision"
        ) from error
    if status.strip():
        raise DirtyRunnerTreeError(
            "benchmark source tree has uncommitted changes; commit them before starting or resuming an experiment"
        )
    return commit


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


def _expected_cleanup_run_id(
    trial_dir: Path,
    result: dict[str, Any] | None,
) -> str | None:
    control = _read_json_object(trial_dir / "control.json")
    if control is not None:
        run_id = control.get("run_id")
        if isinstance(run_id, str) and run_id:
            return run_id
    return _inferred_failed_provision_run_id(result)


def _cleanup_artifact_succeeded(trial_dir: Path, result: dict[str, Any] | None) -> bool:
    expected_run_id = _expected_cleanup_run_id(trial_dir, result)
    if expected_run_id is None:
        return False
    cleanup = _read_json_object(trial_dir / "cleanup.json")
    if not cleanup_payload_proves_inert(
        cleanup,
        expected_run_id=expected_run_id,
    ):
        return False
    return result is None or result.get("cleanup_succeeded") is True


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
        "ProviderInfrastructureError",
        "ProxyError",
        "ReadError",
        "ReadTimeout",
        "RemoteProtocolError",
        "SSLError",
        "TimeoutError",
        "TimeoutExpired",
        "WriteError",
        "WriteTimeout",
        "_StateCaptureHttpError",
    }
)


def _retryable_infrastructure_result(result: dict[str, Any]) -> bool:
    if result.get("terminal") is not True:
        return False
    if result.get("status") == "api_error":
        return result.get("stop_reason") in {
            "http_408",
            "http_429",
            "http_500",
            "http_502",
            "http_503",
            "http_504",
            "http_529",
            "transport_error",
        }
    if result.get("status") != "runtime_error":
        return False
    error_type = result.get("error_type")
    error = str(result.get("error", ""))
    if error_type == "_StateCaptureHttpError":
        return any(f"HTTP {status}" in error for status in (429, 502, 503, 504)) or (
            "admin state returned HTTP 410" in error
        )
    if error_type == "StateCaptureError":
        return "request failed" in error
    return error_type in _RETRYABLE_INFRASTRUCTURE_ERROR_TYPES


def _inferred_failed_provision_run_id(result: dict[str, Any] | None) -> str | None:
    """Recover legacy failed-provision run IDs written before control persistence."""

    if result is None or result.get("status") != "runtime_error" or result.get("error_type") != "ArgaCliError":
        return None
    match = re.search(
        r"\btwin run "
        r"([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}) "
        r"ended in status\b",
        str(result.get("error", "")),
        flags=re.IGNORECASE,
    )
    return match.group(1) if match is not None else None


def _parse_utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _orphan_lease_expiry_evidence(
    result: dict[str, Any] | None,
    *,
    ttl_minutes: int,
) -> dict[str, Any]:
    started_at = _parse_utc_timestamp(result.get("started_at") if result else None)
    if started_at is None or ttl_minutes <= 0:
        return {
            "error_type": "UnsafeResumeNoCleanupIdentity",
            "error": (
                "the prior attempt has no recoverable Arga run ID and no valid timestamped lease-expiry evidence"
            ),
        }

    retry_not_before = started_at + timedelta(
        minutes=ttl_minutes,
        seconds=ORPHAN_TWIN_LEASE_GRACE_SECONDS,
    )
    observed_at = datetime.now(UTC)
    if observed_at < retry_not_before:
        return {
            "error_type": "OrphanTwinLeaseActive",
            "error": (
                "the prior create may have succeeded without persisting its "
                f"Arga run ID; retry is quarantined until {retry_not_before.isoformat()}"
            ),
            "retry_not_before": retry_not_before.isoformat(),
        }

    return {
        "protocol": "arga-bench-orphan-lease-expiry/1",
        "outcome": "lease_window_elapsed",
        "run_id_recoverable": False,
        "attempt_started_at": started_at.isoformat(),
        "ttl_minutes": ttl_minutes,
        "grace_seconds": ORPHAN_TWIN_LEASE_GRACE_SECONDS,
        "retry_not_before": retry_not_before.isoformat(),
        "observed_at": observed_at.isoformat(),
    }


def _orphan_lease_expiry_proves_elapsed(
    payload: dict[str, Any],
    *,
    result: dict[str, Any] | None,
    ttl_minutes: int,
) -> bool:
    if payload.get("protocol") != "arga-bench-orphan-lease-expiry/1":
        return False
    if payload.get("outcome") != "lease_window_elapsed":
        return False
    if payload.get("run_id_recoverable") is not False:
        return False
    if payload.get("ttl_minutes") != ttl_minutes:
        return False
    if payload.get("grace_seconds") != ORPHAN_TWIN_LEASE_GRACE_SECONDS:
        return False

    result_started_at = _parse_utc_timestamp(result.get("started_at") if result else None)
    evidence_started_at = _parse_utc_timestamp(payload.get("attempt_started_at"))
    retry_not_before = _parse_utc_timestamp(payload.get("retry_not_before"))
    observed_at = _parse_utc_timestamp(payload.get("observed_at"))
    if (
        result_started_at is None
        or evidence_started_at != result_started_at
        or retry_not_before is None
        or observed_at is None
    ):
        return False
    expected_not_before = result_started_at + timedelta(
        minutes=ttl_minutes,
        seconds=ORPHAN_TWIN_LEASE_GRACE_SECONDS,
    )
    return (
        retry_not_before == expected_not_before
        and observed_at >= expected_not_before
        and datetime.now(UTC) >= expected_not_before
    )


def _resume_cleanup_payload_proves_safe(
    *,
    trial_dir: Path,
    result: dict[str, Any] | None,
    cleanup_payload: dict[str, Any],
    ttl_minutes: int,
) -> bool:
    expected_run_id = _expected_cleanup_run_id(trial_dir, result)
    if expected_run_id is not None:
        return cleanup_payload_proves_inert(
            cleanup_payload,
            expected_run_id=expected_run_id,
        )
    return _orphan_lease_expiry_proves_elapsed(
        cleanup_payload,
        result=result,
        ttl_minutes=ttl_minutes,
    )


async def _retry_prior_cleanup(
    *,
    trial_dir: Path,
    result: dict[str, Any] | None,
    ttl_minutes: int,
) -> dict[str, Any] | None:
    if _cleanup_artifact_succeeded(trial_dir, result):
        return None

    control_path = trial_dir / "control.json"
    try:
        if control_path.is_file():
            cleanup_payload = await cleanup_instance(control_path)
        elif (run_id := _inferred_failed_provision_run_id(result)) is not None:
            cleanup_payload = await cleanup_twin_run(run_id)
        else:
            cleanup_payload = _orphan_lease_expiry_evidence(
                result,
                ttl_minutes=ttl_minutes,
            )
    except BaseException as cleanup_error:
        cleanup_payload = {
            "error_type": type(cleanup_error).__name__,
            "error": str(cleanup_error),
        }
    write_private_json(trial_dir / "resume-cleanup.json", cleanup_payload)
    return cleanup_payload


async def _prepare_trial_attempt(
    *,
    output_root: Path,
    trial_id: str,
    runner_commit: str,
    ttl_minutes: int = 60,
) -> tuple[Path, int, dict[str, Any] | None]:
    """Return a clean trial directory, preserving and cleaning any stale attempt."""

    trial_dir = output_root / "trials" / trial_id
    result = _read_json_object(trial_dir / "result.json")
    preserve_terminal_result = (
        result is not None and result.get("terminal") is True and not _retryable_infrastructure_result(result)
    )
    if preserve_terminal_result:
        assert result is not None
        existing_cleanup = _read_json_object(trial_dir / "cleanup.json")
        expected_run_id = _expected_cleanup_run_id(trial_dir, result)
        if (
            result.get("cleanup_succeeded") is not True
            and existing_cleanup is not None
            and expected_run_id is not None
            and cleanup_payload_proves_inert(
                existing_cleanup,
                expected_run_id=expected_run_id,
            )
        ):
            result["cleanup"] = existing_cleanup
            result["cleanup_succeeded"] = True
            result["cleanup_reconciled_at"] = _utc_now()
            write_private_json(trial_dir / "result.json", result)
        elif (
            cleanup_payload := await _retry_prior_cleanup(
                trial_dir=trial_dir,
                result=result,
                ttl_minutes=ttl_minutes,
            )
        ) is not None:
            result["cleanup"] = cleanup_payload
            result["cleanup_succeeded"] = _resume_cleanup_payload_proves_safe(
                trial_dir=trial_dir,
                result=result,
                cleanup_payload=cleanup_payload,
                ttl_minutes=ttl_minutes,
            )
            result["cleanup_retried_at"] = _utc_now()
            write_private_json(trial_dir / "cleanup.json", cleanup_payload)
            write_private_json(trial_dir / "result.json", result)

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

    cleanup_payload = await _retry_prior_cleanup(
        trial_dir=trial_dir,
        result=result,
        ttl_minutes=ttl_minutes,
    )
    if cleanup_payload is not None:
        if not _resume_cleanup_payload_proves_safe(
            trial_dir=trial_dir,
            result=result,
            cleanup_payload=cleanup_payload,
            ttl_minutes=ttl_minutes,
        ):
            blocked = {
                "protocol": "arga-bench-trial-result/1",
                "terminal": True,
                "trial_id": trial_id,
                "status": "runtime_error",
                "error_type": cleanup_payload.get(
                    "error_type",
                    "UnsafeResumeBlocked",
                ),
                "error": cleanup_payload.get(
                    "error",
                    "prior attempt cleanup was not proven safe; a fresh twin was not provisioned",
                ),
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
                str(result.get("status"))
                if result is not None and _retryable_infrastructure_result(result)
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
        document.model.binding_id: document.model for document in documents if isinstance(document.model, BindingSpec)
    }
    worlds = {
        document.model.world_id: document.model for document in documents if isinstance(document.model, WorldSpec)
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
    trace_rule_ids = {rule.id for rule in bundle.verification.deterministic.trace_policy.required_calls} | {
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
    relevant = {key: passed for key, passed in grade.assertion_results.items() if key in trace_rule_ids}
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
        ttl_minutes=ttl_minutes,
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
            timeout_seconds=PROVISION_TIMEOUT_SECONDS,
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
        _write_provider_trace(trial_dir / "provider-trace.json", gateway)

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
        if gateway is not None:
            with suppress(Exception):
                _write_provider_trace(trial_dir / "provider-trace.json", gateway)
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
            "tool_calls": len(gateway.trace_records) if gateway is not None else 0,
            "invocation_started": invocation_started,
            "started_at": state["started_at"],
            "finished_at": _utc_now(),
        }
    finally:
        if gateway is not None:
            with suppress(Exception):
                _write_provider_trace(trial_dir / "provider-trace.json", gateway)
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
    expected_run_id = _expected_cleanup_run_id(trial_dir, result)
    result["cleanup_succeeded"] = (
        isinstance(cleanup_payload, dict)
        and expected_run_id is not None
        and cleanup_payload_proves_inert(
            cleanup_payload,
            expected_run_id=expected_run_id,
        )
    )
    write_private_json(result_path, result)
    return result


_IMMUTABLE_SUITE_MANIFEST_FIELDS = (
    "protocol",
    "suite_run_id",
    "experiment_id",
    "repeats",
    "ttl_minutes",
    "orphan_twin_lease_grace_seconds",
    "trial_count",
    "models",
    "trials",
)
_PROMPT_LEDGER_IDENTITY_FIELDS = (
    "protocol",
    "experiment_id",
    "models",
    "entry_count",
    "entries",
)


def _validated_concurrency(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 16:
        raise ValueError(f"{label} must be an integer between 1 and 16")
    return value


def _concurrency_history_entry(
    *,
    event: str,
    concurrency: int,
    recorded_at: str,
    runner_commit: str,
) -> dict[str, Any]:
    return {
        "event": event,
        "concurrency": concurrency,
        "recorded_at": recorded_at,
        "runner_commit": runner_commit,
    }


def _existing_concurrency_history(
    manifest: Mapping[str, Any],
    *,
    initial_concurrency: int,
) -> list[dict[str, Any]]:
    raw_history = manifest.get("concurrency_history")
    if raw_history is None:
        created_at = manifest.get("created_at")
        runner_commit = manifest.get("runner_commit")
        created_timestamp = _parse_utc_timestamp(created_at)
        if not isinstance(created_at, str) or created_timestamp is None:
            raise ValueError("legacy suite manifest cannot backfill concurrency history without created_at")
        if not isinstance(runner_commit, str) or not runner_commit:
            raise ValueError("legacy suite manifest cannot backfill concurrency history without runner_commit")
        created_entry = _concurrency_history_entry(
            event="suite_created",
            concurrency=initial_concurrency,
            recorded_at=created_at,
            runner_commit=runner_commit,
        )
        created_entry["backfilled_from_legacy"] = True
        history = [created_entry]
        last_resumed_at = manifest.get("last_resumed_at")
        if last_resumed_at is not None:
            last_resumed_timestamp = _parse_utc_timestamp(last_resumed_at)
            if not isinstance(last_resumed_at, str) or last_resumed_timestamp is None:
                raise ValueError("legacy suite manifest last_resumed_at must be an aware timestamp")
            if last_resumed_timestamp < created_timestamp:
                raise ValueError("legacy suite manifest last_resumed_at precedes created_at")
            prior_execution_concurrency = _validated_concurrency(
                manifest.get("last_execution_concurrency", initial_concurrency),
                label="legacy suite manifest last_execution_concurrency",
            )
            legacy_resume_commit = runner_commit
            raw_runner_commits = manifest.get("runner_commits")
            if (
                isinstance(raw_runner_commits, list)
                and raw_runner_commits
                and isinstance(raw_runner_commits[-1], str)
                and raw_runner_commits[-1]
            ):
                legacy_resume_commit = raw_runner_commits[-1]
            resume_entry = _concurrency_history_entry(
                event="suite_resumed",
                concurrency=prior_execution_concurrency,
                recorded_at=last_resumed_at,
                runner_commit=legacy_resume_commit,
            )
            resume_entry["backfilled_from_legacy"] = True
            history.append(resume_entry)
        return history
    if not isinstance(raw_history, list) or not raw_history:
        raise ValueError("suite manifest concurrency_history must be a non-empty array")

    history: list[dict[str, Any]] = []
    timestamps: list[datetime] = []
    for index, raw_entry in enumerate(cast(list[object], raw_history)):
        if not isinstance(raw_entry, dict):
            raise ValueError(f"suite manifest concurrency_history[{index}] must be an object")
        entry = cast(dict[str, Any], raw_entry)
        _validated_concurrency(
            entry.get("concurrency"),
            label=f"suite manifest concurrency_history[{index}].concurrency",
        )
        expected_event = "suite_created" if index == 0 else "suite_resumed"
        if entry.get("event") != expected_event:
            raise ValueError(
                f"suite manifest concurrency_history[{index}].event must be {expected_event!r}"
            )
        recorded_at = _parse_utc_timestamp(entry.get("recorded_at"))
        if recorded_at is None:
            raise ValueError(f"suite manifest concurrency_history[{index}].recorded_at must be an aware timestamp")
        if not isinstance(entry.get("runner_commit"), str) or not entry["runner_commit"]:
            raise ValueError(f"suite manifest concurrency_history[{index}].runner_commit must be a non-empty string")
        history.append(dict(entry))
        timestamps.append(recorded_at)
    if history[0]["concurrency"] != initial_concurrency:
        raise ValueError("suite manifest concurrency_history does not preserve the initial concurrency")
    if any(current < previous for previous, current in zip(timestamps, timestamps[1:], strict=False)):
        raise ValueError("suite manifest concurrency_history timestamps are not chronological")
    created_at = _parse_utc_timestamp(manifest.get("created_at"))
    if created_at is None or timestamps[0] != created_at:
        raise ValueError("suite manifest first concurrency history timestamp does not match created_at")
    if history[0]["runner_commit"] != manifest.get("runner_commit"):
        raise ValueError("suite manifest first concurrency history commit does not match runner_commit")
    last_resumed_at = manifest.get("last_resumed_at")
    if len(history) == 1:
        if last_resumed_at is not None:
            raise ValueError("suite manifest last_resumed_at exists without a resume history entry")
    else:
        parsed_last_resumed_at = _parse_utc_timestamp(last_resumed_at)
        if parsed_last_resumed_at is None or parsed_last_resumed_at != timestamps[-1]:
            raise ValueError("suite manifest last concurrency history timestamp does not match last_resumed_at")
    return history


def _merge_resumed_suite_manifest(
    existing_manifest: Mapping[str, Any],
    expected_manifest: Mapping[str, Any],
    *,
    requested_concurrency: int,
    current_runner_commit: str,
    resumed_at: str,
) -> dict[str, Any]:
    """Validate immutable suite identity and append an operational resume record."""

    requested_concurrency = _validated_concurrency(
        requested_concurrency,
        label="requested resume concurrency",
    )
    if not current_runner_commit:
        raise ValueError("current_runner_commit must be a non-empty string")
    resumed_timestamp = _parse_utc_timestamp(resumed_at)
    if resumed_timestamp is None:
        raise ValueError("resumed_at must be an aware timestamp")
    normalized_existing = dict(existing_manifest)
    for legacy_backfill_field in ("ttl_minutes", "orphan_twin_lease_grace_seconds"):
        if legacy_backfill_field not in normalized_existing:
            normalized_existing[legacy_backfill_field] = expected_manifest.get(legacy_backfill_field)
    mismatched = [
        field
        for field in _IMMUTABLE_SUITE_MANIFEST_FIELDS
        if normalized_existing.get(field) != expected_manifest.get(field)
    ]
    suite_run_id = expected_manifest.get("suite_run_id")
    if mismatched:
        raise ValueError(f"cannot resume suite {suite_run_id!r}; manifest fields changed: {', '.join(mismatched)}")

    initial_concurrency = _validated_concurrency(
        normalized_existing.get("concurrency"),
        label="suite manifest concurrency",
    )
    recorded_initial = normalized_existing.get("initial_concurrency", initial_concurrency)
    if _validated_concurrency(recorded_initial, label="suite manifest initial_concurrency") != initial_concurrency:
        raise ValueError("suite manifest initial_concurrency does not match the preserved concurrency")

    history = _existing_concurrency_history(
        normalized_existing,
        initial_concurrency=initial_concurrency,
    )
    recorded_last = normalized_existing.get("last_execution_concurrency", history[-1]["concurrency"])
    if (
        _validated_concurrency(recorded_last, label="suite manifest last_execution_concurrency")
        != history[-1]["concurrency"]
    ):
        raise ValueError("suite manifest last_execution_concurrency does not match concurrency_history")
    previous_timestamp = _parse_utc_timestamp(history[-1]["recorded_at"])
    assert previous_timestamp is not None
    if resumed_timestamp < previous_timestamp:
        raise ValueError("resumed_at precedes the existing concurrency history")

    raw_runner_commits = normalized_existing.get("runner_commits")
    if raw_runner_commits is None:
        raw_runner_commits = [normalized_existing.get("runner_commit")]
    if (
        not isinstance(raw_runner_commits, list)
        or not raw_runner_commits
        or not all(isinstance(revision, str) and revision for revision in cast(list[object], raw_runner_commits))
    ):
        raise ValueError("suite manifest runner_commits must be an array of non-empty strings")
    runner_commits = list(cast(list[str], raw_runner_commits))
    if len(set(runner_commits)) != len(runner_commits):
        raise ValueError("suite manifest runner_commits must not contain duplicates")
    if runner_commits[0] != normalized_existing.get("runner_commit"):
        raise ValueError("suite manifest runner_commits does not begin with runner_commit")
    history_commits = {cast(str, entry["runner_commit"]) for entry in history}
    missing_history_commits = sorted(history_commits - set(runner_commits))
    if missing_history_commits:
        raise ValueError(
            "suite manifest concurrency_history references commits absent from runner_commits: "
            + ", ".join(missing_history_commits)
        )
    if not any(entry.get("backfilled_from_legacy") is True for entry in history):
        ordered_history_commits = list(dict.fromkeys(cast(str, entry["runner_commit"]) for entry in history))
        if ordered_history_commits != runner_commits:
            raise ValueError("suite manifest runner_commits order does not match concurrency_history")

    history.append(
        _concurrency_history_entry(
            event="suite_resumed",
            concurrency=requested_concurrency,
            recorded_at=resumed_at,
            runner_commit=current_runner_commit,
        )
    )
    if current_runner_commit not in runner_commits:
        runner_commits.append(current_runner_commit)

    merged = normalized_existing
    merged["concurrency"] = initial_concurrency
    merged["initial_concurrency"] = initial_concurrency
    merged["last_execution_concurrency"] = requested_concurrency
    merged["concurrency_history"] = history
    merged["runner_commits"] = runner_commits
    merged["last_resumed_at"] = resumed_at
    return merged


def _prompt_ledger_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {field: payload.get(field) for field in _PROMPT_LEDGER_IDENTITY_FIELDS}


def _validate_resume_prompt_ledger(
    existing_ledger: Mapping[str, Any],
    expected_ledger: Mapping[str, Any],
    *,
    suite_run_id: str,
) -> None:
    if _prompt_ledger_identity(existing_ledger) != _prompt_ledger_identity(expected_ledger):
        raise ValueError(f"cannot resume suite {suite_run_id!r}; prompt ledger or experiment content changed")


async def _run_experiment_matrix_locked(
    *,
    catalog_root: Path,
    experiment_id: str,
    output_root: Path,
    model_profiles: tuple[ModelProfile, ...] = MODEL_PROFILES,
    repeats: int = 1,
    concurrency: int = 4,
    ttl_minutes: int = 60,
    suite_run_id: str | None = None,
    runner_commit: str | None = None,
) -> dict[str, Any]:
    concurrency = _validated_concurrency(concurrency, label="concurrency")
    experiment, bundles = load_experiment_bundles(catalog_root, experiment_id)
    suite_run_id = suite_run_id or (
        f"{experiment_id}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    )
    suite_dir = output_root / suite_run_id
    suite_dir.mkdir(parents=True, exist_ok=True)
    ledger = prompt_ledger_payload(catalog_root, experiment_id, model_profiles=model_profiles)
    plans = build_trial_plans(
        experiment,
        suite_run_id=suite_run_id,
        model_profiles=model_profiles,
        repeats=repeats,
    )
    current_runner_commit = runner_commit or _runner_commit()
    created_at = _utc_now()
    manifest: dict[str, Any] = {
        "protocol": "arga-bench-suite/1",
        "suite_run_id": suite_run_id,
        "experiment_id": experiment_id,
        "runner_commit": current_runner_commit,
        "runner_commits": [current_runner_commit],
        "created_at": created_at,
        "repeats": repeats,
        "concurrency": concurrency,
        "initial_concurrency": concurrency,
        "last_execution_concurrency": concurrency,
        "concurrency_history": [
            _concurrency_history_entry(
                event="suite_created",
                concurrency=concurrency,
                recorded_at=created_at,
                runner_commit=current_runner_commit,
            )
        ],
        "ttl_minutes": ttl_minutes,
        "orphan_twin_lease_grace_seconds": ORPHAN_TWIN_LEASE_GRACE_SECONDS,
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
        existing_ledger = _read_json_object(suite_dir / "prompt-ledger.json")
        if existing_ledger is None:
            raise ValueError(f"cannot resume suite {suite_run_id!r}; prompt-ledger.json is missing or invalid")
        _validate_resume_prompt_ledger(
            existing_ledger,
            ledger,
            suite_run_id=suite_run_id,
        )
        manifest = _merge_resumed_suite_manifest(
            existing_mapping,
            manifest,
            requested_concurrency=concurrency,
            current_runner_commit=current_runner_commit,
            resumed_at=_utc_now(),
        )
        write_private_json(manifest_path, manifest)
    else:
        write_private_json(suite_dir / "prompt-ledger.json", ledger)
        markdown_path = suite_dir / "prompt-ledger.md"
        markdown_path.write_text(render_prompt_ledger_markdown(ledger))
        markdown_path.chmod(0o600)
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
        "execution_concurrency": concurrency,
        "concurrency_history_entries": len(cast(list[object], manifest["concurrency_history"])),
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
    """Run one matrix suite while exclusively owning its cross-process lock."""

    concurrency = _validated_concurrency(concurrency, label="concurrency")
    current_runner_commit = _runner_commit()
    resolved_suite_run_id = suite_run_id or (
        f"{experiment_id}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    )
    suite_dir = output_root / resolved_suite_run_id
    suite_dir.mkdir(parents=True, exist_ok=True)
    with _SuiteRunLock(suite_dir / ".matrix.lock"):
        return await _run_experiment_matrix_locked(
            catalog_root=catalog_root,
            experiment_id=experiment_id,
            output_root=output_root,
            model_profiles=model_profiles,
            repeats=repeats,
            concurrency=concurrency,
            ttl_minutes=ttl_minutes,
            suite_run_id=resolved_suite_run_id,
            runner_commit=current_runner_commit,
        )
