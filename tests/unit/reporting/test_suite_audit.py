from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
            "trial_count": 1,
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
    assert report["scoring_ready"] is True
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
