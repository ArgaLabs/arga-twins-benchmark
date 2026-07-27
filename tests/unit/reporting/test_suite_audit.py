from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from arga_twins_benchmark.reporting.suite_audit import audit_suite

SYSTEM_PROMPT = "Use the provisioned provider only."
USER_PROMPT = "Inspect six facts and return JSON."
RUN_ID = "11111111-2222-3333-4444-555555555555"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


def _build_suite(tmp_path: Path, *, broken: bool = False) -> Path:
    suite_dir = tmp_path / "suite"
    trial_id = "suite-1--r1--instance-1--model-1"
    trial_dir = suite_dir / "trials" / trial_id
    plan_model = {
        "model_id": "model-1",
        "label": "Model 1",
        "provider": "test",
        "effort": "high",
        "thinking": "reasoning",
        "fallback": False,
    }
    _write_json(
        suite_dir / "suite.json",
        {
            "protocol": "arga-bench-suite/1",
            "suite_run_id": "suite-1",
            "experiment_id": "experiment-1",
            "runner_commit": "commit-1",
            "runner_commits": ["commit-1"],
            "created_at": "2030-01-01T00:00:00+00:00",
            "trial_count": 1,
            "concurrency": 4,
            "initial_concurrency": 4,
            "last_execution_concurrency": 4,
            "concurrency_history": [
                {
                    "event": "suite_created",
                    "concurrency": 4,
                    "recorded_at": "2030-01-01T00:00:00+00:00",
                    "runner_commit": "commit-1",
                }
            ],
            "trials": [
                {
                    "trial_id": trial_id,
                    "suite_run_id": "suite-1",
                    "instance_id": "instance-1",
                    "repeat": 1,
                    "model": plan_model,
                }
            ],
        },
    )
    _write_json(
        suite_dir / "prompt-ledger.json",
        {
            "protocol": "arga-bench-prompt-ledger/1",
            "entries": [
                {
                    "instance_id": "instance-1",
                    "model_id": "model-1",
                    "model_label": "Model 1",
                    "system_prompt": SYSTEM_PROMPT,
                    "system_prompt_sha256": _sha256(SYSTEM_PROMPT),
                    "user_prompt": USER_PROMPT,
                    "user_prompt_sha256": _sha256(USER_PROMPT),
                }
            ],
        },
    )
    _write_json(
        trial_dir / "prompt.json",
        {
            "model": plan_model,
            "system_prompt": "tampered" if broken else SYSTEM_PROMPT,
            "user_prompt": USER_PROMPT,
        },
    )
    trace_events = [
        {
            "sequence": index,
            "provider": "external" if broken and index == 5 else "github",
            "requested_provider": "github",
            "method": "GET",
            "path": "/admin/state" if broken and index == 4 else f"/repos/acme/demo/issues/{index}",
            "status_code": 200,
        }
        for index in range(1, 7 if not broken else 6)
    ]
    _write_json(trial_dir / "provider-trace.json", {"events": trace_events})
    _write_json(
        trial_dir / "candidate-access.json",
        {
            "provider_access": {
                "github": {
                    "base_url": ("https://pub-r11111111222233334444555555555555--github.sandbox.argalabs.com"),
                    "env": {},
                }
            }
        },
    )
    cleanup_run_id = "other-run" if broken else RUN_ID
    cleanup: dict[str, Any] = {
        "twin_run": {
            "run_id": cleanup_run_id,
            "status": "cleaning_up" if broken else "cancelled",
            "twins": {} if not broken else {"github": {}},
        }
    }
    _write_json(trial_dir / "control.json", {"run_id": RUN_ID, "scenario_id": "scenario-1"})
    _write_json(trial_dir / "cleanup.json", cleanup)
    _write_json(trial_dir / "state.json", {"cleanup": cleanup})
    response_model = "model-fallback" if broken else "model-1"
    result_model = {**plan_model, "fallback": broken}
    result: dict[str, Any] = {
        "protocol": "arga-bench-trial-result/1",
        "terminal": True,
        "trial_id": trial_id,
        "suite_run_id": "suite-1",
        "instance_id": "instance-1",
        "model": result_model,
        "response_model": response_model,
        "status": "completed",
        "tool_calls": len(trace_events),
        "invocation_started": True,
        "state_grade_complete": not broken,
        "cleanup": cleanup,
        "cleanup_succeeded": not broken,
    }
    _write_json(trial_dir / "result.json", result)
    _write_json(
        trial_dir / "invocation.json",
        {
            "config": {"model": "model-1", "fallback": True if broken else None},
            "events": [
                {
                    "type": "assistant_response",
                    "response_model": response_model,
                }
            ],
        },
    )
    return suite_dir


def test_audit_suite_accepts_complete_exact_local_evidence(tmp_path: Path) -> None:
    report = audit_suite(_build_suite(tmp_path))

    assert report["suite_complete"] is True
    assert report["matrix_fully_evaluable"] is True
    assert report["integrity_passed"] is True
    assert report["benchmark_contract_passed"] is True
    assert report["trajectory_diagnostics_passed"] is True
    assert report["destination_safety_passed"] is True
    assert report["scoring_ready"] is True
    assert report["concurrency"] == {
        "original_concurrency": 4,
        "initial_concurrency": 4,
        "last_execution_concurrency": 4,
        "history_entries": 1,
        "legacy_backfilled": False,
        "valid": True,
        "issues": [],
    }
    assert report["by_model"] == {
        "model-1": {
            "expected": 1,
            "statuses": {"completed": 1},
        }
    }
    assert all(check["violation_count"] == 0 for check in report["checks"].values())


def test_audit_suite_reports_each_integrity_and_readiness_failure(tmp_path: Path) -> None:
    report = audit_suite(_build_suite(tmp_path, broken=True))
    checks = report["checks"]

    assert report["suite_complete"] is True
    assert report["integrity_passed"] is False
    assert report["benchmark_contract_passed"] is False
    assert report["trajectory_diagnostics_passed"] is False
    assert report["destination_safety_passed"] is False
    assert report["scoring_ready"] is False
    assert checks["response_model"]["violation_count"] == 1
    assert checks["prompt_hash"]["violation_count"] == 1
    assert checks["no_fallback"]["violation_count"] == 1
    assert checks["tool_call_minimum"]["violation_count"] == 1
    assert checks["provider_trace_destination"]["violation_count"] == 1
    assert checks["provider_trace_control_plane"]["violation_count"] == 1
    assert checks["cleanup_identity"]["violation_count"] == 1
    assert checks["cleanup_inert"]["violation_count"] == 1
    assert checks["state_grade_completeness"]["violation_count"] == 1


def test_under_minimum_trace_is_diagnostic_and_remains_scoring_ready(tmp_path: Path) -> None:
    suite_dir = _build_suite(tmp_path)
    trial_dir = next((suite_dir / "trials").iterdir())
    trace_path = trial_dir / "provider-trace.json"
    trace = json.loads(trace_path.read_text())
    trace["events"] = trace["events"][:5]
    _write_json(trace_path, trace)
    result_path = trial_dir / "result.json"
    result = json.loads(result_path.read_text())
    result["tool_calls"] = 5
    _write_json(result_path, result)

    report = audit_suite(suite_dir)

    assert report["checks"]["tool_call_minimum"]["violation_count"] == 1
    assert report["trajectory_diagnostics_passed"] is False
    assert report["integrity_passed"] is True
    assert report["benchmark_contract_passed"] is True
    assert report["scoring_ready"] is True


def test_destination_safety_violation_is_scoreable_agent_behavior(tmp_path: Path) -> None:
    suite_dir = _build_suite(tmp_path)
    trial_dir = next((suite_dir / "trials").iterdir())
    trace_path = trial_dir / "provider-trace.json"
    trace = json.loads(trace_path.read_text())
    trace["events"][0]["provider"] = "external"
    trace["events"][1]["path"] = "/_ui/repos/acme/demo"
    _write_json(trace_path, trace)

    report = audit_suite(suite_dir)

    assert report["checks"]["provider_trace_destination"]["violation_count"] == 1
    assert report["checks"]["provider_trace_control_plane"]["violation_count"] == 1
    assert report["destination_safety_passed"] is False
    assert report["integrity_passed"] is True
    assert report["benchmark_contract_passed"] is True
    assert report["scoring_ready"] is True


def test_missing_provider_trace_remains_an_integrity_failure(tmp_path: Path) -> None:
    suite_dir = _build_suite(tmp_path)
    trial_dir = next((suite_dir / "trials").iterdir())
    (trial_dir / "provider-trace.json").unlink()

    report = audit_suite(suite_dir)

    assert report["checks"]["provider_trace_integrity"]["violation_count"] == 1
    assert report["integrity_passed"] is False
    assert report["scoring_ready"] is False


@pytest.mark.parametrize(
    ("corruption", "value"),
    [
        ("protocol", "unsupported"),
        ("sequence", 1),
        ("method", 7),
        ("status_code", True),
        ("action_fingerprint", "not-a-digest"),
    ],
)
def test_malformed_provider_trace_is_an_integrity_failure(
    tmp_path: Path,
    corruption: str,
    value: object,
) -> None:
    suite_dir = _build_suite(tmp_path)
    trial_dir = next((suite_dir / "trials").iterdir())
    trace_path = trial_dir / "provider-trace.json"
    trace = json.loads(trace_path.read_text())
    if corruption == "protocol":
        trace["protocol"] = value
    elif corruption == "sequence":
        trace["events"][1]["sequence"] = value
    else:
        trace["events"][0][corruption] = value
    _write_json(trace_path, trace)

    report = audit_suite(suite_dir)

    assert report["checks"]["provider_trace_integrity"]["violation_count"] == 1
    assert report["integrity_passed"] is False
    assert report["destination_safety_passed"] is False
    assert report["scoring_ready"] is False


def test_audit_safely_backfills_a_legacy_concurrency_manifest(tmp_path: Path) -> None:
    suite_dir = _build_suite(tmp_path)
    manifest_path = suite_dir / "suite.json"
    manifest = json.loads(manifest_path.read_text())
    for field in ("initial_concurrency", "last_execution_concurrency", "concurrency_history"):
        del manifest[field]
    manifest["runner_commits"] = ["commit-1", "commit-2"]
    manifest["last_resumed_at"] = "2030-01-02T00:00:00+00:00"
    _write_json(manifest_path, manifest)

    report = audit_suite(suite_dir)

    assert report["integrity_passed"] is True
    assert report["concurrency"] == {
        "original_concurrency": 4,
        "initial_concurrency": 4,
        "last_execution_concurrency": 4,
        "history_entries": 2,
        "legacy_backfilled": True,
        "valid": True,
        "issues": [],
    }


def test_audit_rejects_invalid_concurrency_events_and_timestamp_order(tmp_path: Path) -> None:
    suite_dir = _build_suite(tmp_path)
    manifest_path = suite_dir / "suite.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["last_execution_concurrency"] = 2
    manifest["last_resumed_at"] = "2029-12-31T00:00:00+00:00"
    manifest["concurrency_history"].append(
        {
            "event": "arbitrary_event",
            "concurrency": 2,
            "recorded_at": "2029-12-31T00:00:00+00:00",
            "runner_commit": "commit-1",
        }
    )
    _write_json(manifest_path, manifest)

    report = audit_suite(suite_dir)

    assert report["integrity_passed"] is False
    assert report["checks"]["concurrency_history"]["violation_count"] == 1
    issues = report["concurrency"]["issues"]
    assert "concurrency_history[1].event must be 'suite_resumed'" in issues
    assert "concurrency history timestamps are not chronological" in issues


def test_missing_trial_does_not_reduce_suite_level_concurrency_audit_coverage(tmp_path: Path) -> None:
    suite_dir = _build_suite(tmp_path)
    next((suite_dir / "trials").glob("*/result.json")).unlink()

    report = audit_suite(suite_dir)

    assert report["checks"]["concurrency_history"] == {
        "checked": 1,
        "passed_count": 1,
        "not_applicable": 0,
        "violations": [],
        "violation_count": 0,
        "passed": True,
        "coverage_complete": True,
    }


def test_audit_validates_split_docs_counts_and_suite_cache_body(tmp_path: Path) -> None:
    suite_dir = _build_suite(tmp_path)
    trial_dir = next((suite_dir / "trials").iterdir())
    body = b"Official GitHub REST documentation bytes"
    digest = __import__("hashlib").sha256(body).hexdigest()
    source_url = "https://docs.github.com/en/rest/pulls/pulls?apiVersion=2022-11-28"
    _write_json(
        trial_dir / "official-docs-trace.json",
        {
            "protocol": "arga-bench-official-docs-trace/1",
            "events": [
                {
                    "sequence": 1,
                    "started_at": "2030-01-01T00:00:00+00:00",
                    "requested_provider": "github",
                    "provider": "github",
                    "action": "fetch",
                    "doc_id": "pull-requests",
                    "source_url": source_url,
                    "final_url": source_url,
                    "status_code": 200,
                    "latency_ms": 1,
                    "response_bytes": len(body),
                    "content_sha256": digest,
                    "truncated": False,
                    "cache_hit": False,
                    "query_present": False,
                    "error": None,
                }
            ],
        },
    )
    cache_root = suite_dir / "official-docs-cache"
    body_path = cache_root / "responses" / f"{digest}.body"
    body_path.parent.mkdir(parents=True)
    body_path.write_bytes(body)
    _write_json(
        cache_root / "manifest.json",
        {
            "protocol": "arga-bench-official-docs-cache/1",
            "entry_count": 1,
            "entries": [
                {
                    "provider": "github",
                    "requested_url": source_url,
                    "http_status": 200,
                    "response_bytes": len(body),
                    "content_sha256": digest,
                    "body_file": f"responses/{digest}.body",
                }
            ],
        },
    )
    result_path = trial_dir / "result.json"
    result = json.loads(result_path.read_text())
    result["provider_tool_calls"] = 6
    result["official_docs_tool_calls"] = 1
    result["tool_calls"] = 7
    _write_json(result_path, result)

    report = audit_suite(suite_dir)

    assert report["checks"]["tool_call_count_consistency"]["violation_count"] == 0
    assert report["checks"]["provider_trace_integrity"]["violation_count"] == 0
    assert report["integrity_passed"] is True

    body_path.write_bytes(b"tampered")
    corrupted = audit_suite(suite_dir)
    assert corrupted["checks"]["provider_trace_integrity"]["violation_count"] == 1
    assert corrupted["integrity_passed"] is False


def test_audit_accepts_legacy_suite_without_docs_trace_or_split_counts(tmp_path: Path) -> None:
    report = audit_suite(_build_suite(tmp_path))

    assert report["checks"]["tool_call_count_consistency"]["violation_count"] == 0
    assert report["checks"]["provider_trace_integrity"]["violation_count"] == 0
