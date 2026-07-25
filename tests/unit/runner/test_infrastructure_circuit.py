from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from arga_twins_benchmark.evaluation.state_capture import TrustedStateSnapshot
from arga_twins_benchmark.providers import ProviderInfrastructureError, ProviderTraceRecord
from arga_twins_benchmark.runner.matrix import (
    TrialPlan,
    _prepare_trial_attempt,  # pyright: ignore[reportPrivateUsage]
    _retryable_infrastructure_result,  # pyright: ignore[reportPrivateUsage]
    load_experiment_bundles,
    run_trial,
)
from arga_twins_benchmark.runner.prompting import MODEL_PROFILES


class _FailingGateway:
    def __init__(
        self,
        _provider_access: Mapping[str, Mapping[str, object]],
        *,
        provider_roles: Mapping[str, str],
    ) -> None:
        del provider_roles
        self._trace_records: list[ProviderTraceRecord] = []
        self.tool_definition: dict[str, object] = {
            "name": "provider_api",
            "description": "test provider tool",
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        }

    @property
    def trace_records(self) -> tuple[ProviderTraceRecord, ...]:
        return tuple(self._trace_records)

    async def execute(self, _tool_input: Mapping[str, object]) -> dict[str, object]:
        self._trace_records.append(
            ProviderTraceRecord(
                sequence=1,
                started_at="2030-01-01T00:00:00+00:00",
                requested_provider="code_host",
                provider="github",
                method="GET",
                path="/repos/acme/app",
                operation=None,
                operation_type=None,
                status_code=410,
                latency_ms=5,
                response_bytes=316,
                truncated=False,
                error=None,
            )
        )
        raise ProviderInfrastructureError(
            code="environment_destroyed",
            status_code=410,
            consecutive_failures=1,
        )

    async def aclose(self) -> None:
        return None


class _StateCapturer:
    async def capture(
        self,
        _control_payload: Mapping[str, object],
        *,
        roles: Mapping[str, str],
        snapshot_queries: object,
    ) -> TrustedStateSnapshot:
        del roles, snapshot_queries
        return TrustedStateSnapshot(providers={})


def test_run_trial_persists_trace_and_marks_provider_circuit_failure_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance_id = "blocking_code_review_v1_github_clean_001"
    _, bundles = load_experiment_bundles(Path("benchmark"), "development_pilot_48_v1")
    bundle = bundles[instance_id]
    plan = TrialPlan(
        suite_run_id="suite-1",
        trial_id="trial-1",
        repeat=1,
        instance_id=instance_id,
        model=MODEL_PROFILES[0],
    )

    async def fake_provision_instance(
        *,
        catalog_root: Path,
        instance_id: str,
        control_output: Path,
        candidate_output: Path,
        ttl_minutes: int,
        timeout_seconds: int,
    ) -> None:
        del catalog_root, instance_id, ttl_minutes, timeout_seconds
        control_output.write_text(
            json.dumps(
                {
                    "protocol": "arga-bench-control/1",
                    "scenario_id": "scenario-1",
                    "run_id": "run-1",
                    "twin_run": {
                        "run_id": "run-1",
                        "status": "ready",
                        "twins": {},
                    },
                }
            )
        )
        candidate_output.write_text(
            json.dumps(
                {
                    "protocol": "arga-bench-candidate-access/1",
                    "provider_access": {
                        "github": {
                            "base_url": "https://pub-run--github.sandbox.argalabs.com",
                            "env": {},
                        }
                    },
                }
            )
        )

    async def fake_invoke_model(*_args: object, **kwargs: object) -> object:
        executor = cast(
            Callable[[str, dict[str, Any]], Awaitable[object]],
            kwargs["execute_tool"],
        )
        return await executor(
            "provider_api",
            {
                "provider": "code_host",
                "method": "GET",
                "path": "/repos/acme/app",
            },
        )

    async def fake_cleanup_instance(_control_path: Path) -> dict[str, Any]:
        return {
            "twin_run": {
                "run_id": "run-1",
                "status": "cancelled",
                "twins": {},
            }
        }

    monkeypatch.setattr("arga_twins_benchmark.runner.matrix.provision_instance", fake_provision_instance)
    monkeypatch.setattr("arga_twins_benchmark.runner.matrix.ProviderGateway", _FailingGateway)
    monkeypatch.setattr("arga_twins_benchmark.runner.matrix.TrustedStateCapturer", _StateCapturer)
    monkeypatch.setattr("arga_twins_benchmark.runner.matrix.invoke_model", fake_invoke_model)
    monkeypatch.setattr("arga_twins_benchmark.runner.matrix.cleanup_instance", fake_cleanup_instance)

    result = asyncio.run(
        run_trial(
            catalog_root=Path("benchmark"),
            bundle=bundle,
            plan=plan,
            output_root=tmp_path,
            runner_commit="test-commit",
        )
    )

    assert result["status"] == "runtime_error"
    assert result["error_type"] == "ProviderInfrastructureError"
    assert result["tool_calls"] == 1
    assert result["cleanup_succeeded"] is True
    assert _retryable_infrastructure_result(result) is True
    trace = json.loads((tmp_path / "trials" / "trial-1" / "provider-trace.json").read_text())
    assert trace["protocol"] == "arga-bench-provider-trace/1"
    assert len(trace["events"]) == 1
    assert trace["events"][0]["status_code"] == 410

    prepared_dir, attempt, existing = asyncio.run(
        _prepare_trial_attempt(
            output_root=tmp_path,
            trial_id="trial-1",
            runner_commit="resume-commit",
        )
    )

    assert prepared_dir == tmp_path / "trials" / "trial-1"
    assert attempt == 2
    assert existing is None
    archived = tmp_path / "attempts" / "trial-1" / "attempt-0001"
    assert json.loads((archived / "attempt.json").read_text())["archive_reason"] == "runtime_error"
    assert json.loads((archived / "provider-trace.json").read_text()) == trace
