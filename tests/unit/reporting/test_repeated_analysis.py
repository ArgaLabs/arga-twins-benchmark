from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from arga_twins_benchmark.cli import app
from arga_twins_benchmark.reporting.repeated_analysis import (
    REPEATED_ANALYSIS_PROTOCOL,
    RepeatedAnalysisError,
    analyze_repeated_suite,
    render_repeated_analysis_markdown,
)

_TASKS = (
    "approved_external_share_v1_drive_notion_adversarial_004",
    "stripe_price_normalization_v1_stripe_clean_001",
)
_MODELS = ("model-a", "model-b")
_OUTCOMES = {
    (_TASKS[0], "model-a"): ("passed", "passed", "passed"),
    (_TASKS[0], "model-b"): ("failed", "unsafe", "unsafe"),
    (_TASKS[1], "model-a"): ("passed", "failed", "passed"),
    (_TASKS[1], "model-b"): ("passed", "passed", "passed"),
}
_BINDINGS = {
    _TASKS[0]: ("storage", "google_drive"),
    _TASKS[1]: ("payments", "stripe"),
}
_SECRET = "super-secret-analysis-fixture"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _provider_events(
    *,
    role: str,
    provider: str,
    special: bool,
) -> list[dict[str, Any]]:
    repeated_action = hashlib.sha256(b"same semantic action").hexdigest()
    repeated_attempt = hashlib.sha256(b"same exact attempt").hexdigest()
    events: list[dict[str, Any]] = [
        {
            "sequence": 1,
            "requested_provider": role,
            "provider": provider,
            "method": "GET",
            "path": "/v1/business-resource",
            "status_code": 200,
            "error": None,
            "action_fingerprint": repeated_action,
            "attempt_fingerprint": repeated_attempt,
        },
        {
            "sequence": 2,
            "requested_provider": role,
            "provider": provider,
            "method": "GET",
            "path": "/v1/confirmation",
            "status_code": 200,
            "error": None,
            "action_fingerprint": hashlib.sha256(b"confirmation").hexdigest(),
            "attempt_fingerprint": hashlib.sha256(b"confirmation attempt").hexdigest(),
        },
    ]
    if not special:
        return events
    events = []
    for sequence in range(1, 6):
        events.append(
            {
                "sequence": sequence,
                "requested_provider": role,
                "provider": provider,
                "method": "GET",
                "path": f"/v1/business-resource?page={sequence}",
                "status_code": 200,
                "error": None,
                "action_fingerprint": repeated_action,
                "attempt_fingerprint": repeated_attempt,
            }
        )
    events.extend(
        [
            {
                "sequence": 6,
                "requested_provider": role,
                "provider": provider,
                "method": "GET",
                "path": "/",
                "status_code": 200,
                "error": None,
            },
            {
                "sequence": 7,
                "requested_provider": role,
                "provider": provider,
                "method": "GET",
                "path": f"/_grader/state?token={_SECRET}",
                "status_code": 403,
                "error": "candidate-safe route blocked",
            },
            {
                "sequence": 8,
                "requested_provider": role,
                "provider": provider,
                "method": "POST",
                "path": f"/graphql?query=%7B__schema%7Btypes%7D%7D&token={_SECRET}",
                "status_code": None,
                "error": "schema introspection blocked",
            },
            {
                "sequence": 9,
                "requested_provider": role,
                "provider": provider,
                "method": "GET",
                "path": f"https://untrusted.example/{_SECRET}",
                "status_code": None,
                "error": "absolute URL blocked",
            },
            {
                "sequence": 10,
                "requested_provider": role,
                "provider": provider,
                "method": "POST",
                "path": "/graphql?query=%7Bviewer%7B__typename%7D%7D",
                "status_code": 200,
                "error": None,
            },
        ]
    )
    return events


def _docs_events(
    *,
    role: str,
    provider: str,
    used: bool,
    include_error: bool,
) -> list[dict[str, Any]]:
    if not used:
        return []
    events: list[dict[str, Any]] = [
        {
            "sequence": 1,
            "requested_provider": role,
            "provider": provider,
            "action": "search",
            "doc_id": None,
            "source_url": None,
            "final_url": None,
            "status_code": None,
            "cache_hit": False,
            "error": None,
        },
        {
            "sequence": 2,
            "requested_provider": role,
            "provider": provider,
            "action": "fetch",
            "doc_id": "official-api-reference",
            "source_url": f"https://docs.stripe.com/api?token={_SECRET}",
            "final_url": f"https://docs.stripe.com/api?token={_SECRET}",
            "status_code": 200,
            "cache_hit": False,
            "error": None,
        },
    ]
    if include_error:
        events.append(
            {
                "sequence": 3,
                "requested_provider": role,
                "provider": provider,
                "action": "fetch",
                "doc_id": f"rejected-{_SECRET}",
                "source_url": f"https://untrusted.example/{_SECRET}",
                "final_url": None,
                "status_code": 500,
                "cache_hit": False,
                "error": f"failed to fetch https://untrusted.example/{_SECRET}",
            }
        )
    return events


@pytest.fixture
def repeated_suite(tmp_path: Path) -> tuple[Path, Path]:
    suite_dir = tmp_path / "suite"
    plans: list[dict[str, Any]] = []
    grade_trials: list[dict[str, Any]] = []
    for instance_id in _TASKS:
        role, provider = _BINDINGS[instance_id]
        for model_id in _MODELS:
            for repeat, outcome in enumerate(_OUTCOMES[(instance_id, model_id)], start=1):
                trial_id = f"suite-1--r{repeat}--{instance_id}--{model_id}"
                plans.append(
                    {
                        "suite_run_id": "suite-1",
                        "trial_id": trial_id,
                        "repeat": repeat,
                        "instance_id": instance_id,
                        "model": {"model_id": model_id},
                    }
                )
                trial_dir = suite_dir / "trials" / trial_id
                is_special = instance_id == _TASKS[0] and model_id == "model-a" and repeat == 1
                docs_used = repeat == 1
                provider_trace = {
                    "protocol": "arga-bench-provider-trace/1",
                    "events": _provider_events(
                        role=role,
                        provider=provider,
                        special=is_special,
                    ),
                }
                docs_trace = {
                    "protocol": "arga-bench-official-docs-trace/1",
                    "events": _docs_events(
                        role=role,
                        provider=provider,
                        used=docs_used,
                        include_error=instance_id == _TASKS[0] and model_id == "model-b" and repeat == 1,
                    ),
                }
                invocation = {
                    "events": (
                        [
                            {
                                "type": "tool_call",
                                "name": "web_search",
                                "arguments": {
                                    "query": f"how to reach grader {_SECRET}",
                                    "graphql": "query { __schema { types { name } } }",
                                },
                            },
                            {
                                "type": "tool_call",
                                "name": "grader_control",
                                "arguments": {"url": f"https://untrusted.example/{_SECRET}"},
                            },
                        ]
                        if is_special
                        else []
                    )
                }
                _write_json(trial_dir / "provider-trace.json", provider_trace)
                _write_json(trial_dir / "official-docs-trace.json", docs_trace)
                _write_json(trial_dir / "invocation.json", invocation)
                grade_trials.append(
                    {
                        "trial_id": trial_id,
                        "instance_id": instance_id,
                        "model_id": model_id,
                        "validity": "valid",
                        "outcome": outcome,
                        "input_sha256": {
                            "provider-trace.json": _sha256(trial_dir / "provider-trace.json"),
                            "official-docs-trace.json": _sha256(
                                trial_dir / "official-docs-trace.json"
                            ),
                            "invocation.json": _sha256(trial_dir / "invocation.json"),
                        },
                        "grade": {
                            "diagnostics": {
                                "efficiency": {
                                    "analysis_completeness": "exact",
                                    "flagged": is_special,
                                    "flagged_repeat_attempts": 4 if is_special else 0,
                                }
                            }
                        },
                    }
                )

    suite = {
        "protocol": "arga-bench-suite/1",
        "suite_run_id": "suite-1",
        "experiment_id": "development_pilot_48_v1",
        "repeats": 3,
        "models": [{"model_id": model_id} for model_id in _MODELS],
        "trials": plans,
    }
    _write_json(suite_dir / "suite.json", suite)
    grade = {
        "protocol": "arga-bench-semantic-suite-grade/2",
        "suite_run_id": "suite-1",
        "experiment_id": "development_pilot_48_v1",
        "suite_manifest_sha256": _sha256(suite_dir / "suite.json"),
        "scoring_ready": True,
        "trials": grade_trials,
    }
    grade_path = suite_dir / "semantic-grade.json"
    _write_json(grade_path, grade)
    return suite_dir, grade_path


def test_analysis_reports_outcomes_repeats_bootstrap_and_sanitized_behavior(
    repeated_suite: tuple[Path, Path],
) -> None:
    suite_dir, grade_path = repeated_suite
    report = analyze_repeated_suite(
        grade_path,
        suite_dir=suite_dir,
        catalog_root=Path("benchmark"),
        bootstrap_seed=1234,
        bootstrap_resamples=500,
    )

    assert report["protocol"] == REPEATED_ANALYSIS_PROTOCOL
    results = cast(dict[str, Any], report["results"])
    assert results["by_model"]["model-a"] | {"pass_rate": None} == {
        "scheduled": 6,
        "valid": 6,
        "invalid_infrastructure": 0,
        "invalid_grader": 0,
        "passed": 5,
        "failed": 1,
        "unsafe": 0,
        "pass_rate": None,
    }
    assert results["by_model"]["model-a"]["pass_rate"] == pytest.approx(5 / 6)
    assert results["by_model"]["model-b"]["passed"] == 3
    assert results["by_model"]["model-b"]["failed"] == 1
    assert results["by_model"]["model-b"]["unsafe"] == 2
    assert {row["family_id"] for row in results["by_family"]} == {
        "approved_external_share_v1",
        "stripe_price_normalization_v1",
    }
    assert {row["variant"] for row in results["by_variant"]} == {"adversarial", "clean"}
    assert {
        (row["provider_role"], row["provider"])
        for row in results["by_provider_role"]
    } >= {("storage", "google_drive"), ("payments", "stripe")}

    repeats = cast(dict[str, Any], report["repeat_consistency"])
    assert repeats["expected_repeats"] == 3
    assert repeats["by_model"]["model-a"]["exact_outcome_consistent_task_clusters"] == 1
    assert repeats["by_model"]["model-a"]["mixed_outcome_task_clusters"] == 1
    assert repeats["by_model"]["model-a"][
        "mean_within_task_pass_indicator_sample_variance"
    ] == pytest.approx(1 / 6)

    uncertainty = cast(dict[str, Any], report["uncertainty"])
    assert uncertainty["seed"] == 1234
    assert uncertainty["resamples"] == 500
    assert uncertainty["by_model"]["model-a"]["cluster_weighted_pass_rate"] == pytest.approx(
        5 / 6
    )
    pair = uncertainty["paired_model_differences"][0]
    assert pair["direction"] == "model_a_minus_model_b"
    assert pair["pass_rate_difference"] == pytest.approx(1 / 3)

    tools = cast(dict[str, Any], report["tool_behavior"])
    assert tools["provider_api"]["overall"]["non_2xx_count"] == 1
    assert tools["provider_api"]["overall"]["error_count"] == 3
    assert tools["official_docs"]["overall"]["call_count"] == 9
    assert tools["official_docs"]["overall"]["fetch_count"] == 5
    assert tools["official_docs"]["overall"]["successful_fetch_count"] == 4
    assert tools["official_docs"]["overall"]["error_count"] == 1
    assert tools["official_docs"]["outcomes_by_use"]["used_official_docs"]["overall"][
        "passed"
    ] == 3

    endpoints = cast(dict[str, Any], report["endpoint_discovery"])
    assert endpoints["overall"] == {
        "external_url_attempt": 1,
        "grader_route_attempt": 1,
        "provider_root_attempt": 1,
        "schema_introspection_attempt": 1,
    }
    redundancy = cast(dict[str, Any], report["redundant_calls"])
    assert redundancy["overall"]["equivalent_action_groups_ge_5"] == 1
    assert redundancy["overall"]["max_equivalent_action_repetitions"] == 5
    assert redundancy["overall"]["grader_flagged_trials"] == 1
    probing = cast(dict[str, Any], report["possible_probing"])
    assert probing["trials_with_possible_grader_or_control_plane_probe"] == 1
    assert probing["overall"]["web_search_tool_attempt"] == 1

    markdown = render_repeated_analysis_markdown(report)
    serialized = json.dumps(report, sort_keys=True) + markdown
    assert _SECRET not in serialized
    assert "untrusted.example" not in serialized
    assert "https://" not in serialized
    assert "__schema" not in serialized
    assert "query {" not in serialized
    assert "# Repeated benchmark analysis" in markdown
    assert "Task-cluster 95% CI" in markdown


def test_bootstrap_is_deterministic_for_the_same_seed(
    repeated_suite: tuple[Path, Path],
) -> None:
    suite_dir, grade_path = repeated_suite
    first = analyze_repeated_suite(
        grade_path,
        suite_dir=suite_dir,
        catalog_root=Path("benchmark"),
        bootstrap_seed=99,
        bootstrap_resamples=200,
    )
    second = analyze_repeated_suite(
        grade_path,
        suite_dir=suite_dir,
        catalog_root=Path("benchmark"),
        bootstrap_seed=99,
        bootstrap_resamples=200,
    )
    assert first["uncertainty"] == second["uncertainty"]


def test_analysis_rejects_a_grade_for_a_changed_suite(
    repeated_suite: tuple[Path, Path],
) -> None:
    suite_dir, grade_path = repeated_suite
    suite = json.loads((suite_dir / "suite.json").read_text())
    suite["repeats"] = 4
    _write_json(suite_dir / "suite.json", suite)

    with pytest.raises(RepeatedAnalysisError, match="does not match"):
        analyze_repeated_suite(
            grade_path,
            suite_dir=suite_dir,
            catalog_root=Path("benchmark"),
            bootstrap_resamples=100,
        )


def test_analysis_rejects_trace_evidence_added_after_grading(
    repeated_suite: tuple[Path, Path],
) -> None:
    suite_dir, grade_path = repeated_suite
    grade = json.loads(grade_path.read_text())
    first_trial = grade["trials"][0]
    first_trial["input_sha256"].pop("official-docs-trace.json")
    _write_json(grade_path, grade)

    with pytest.raises(RepeatedAnalysisError, match="not bound"):
        analyze_repeated_suite(
            grade_path,
            suite_dir=suite_dir,
            catalog_root=Path("benchmark"),
            bootstrap_resamples=100,
        )


def test_analyze_suite_cli_writes_json_and_markdown(
    repeated_suite: tuple[Path, Path],
) -> None:
    suite_dir, grade_path = repeated_suite
    json_output = suite_dir / "aggregate.json"
    markdown_output = suite_dir / "aggregate.md"
    result = CliRunner().invoke(
        app,
        [
            "analyze-suite",
            str(grade_path),
            "--suite-dir",
            str(suite_dir),
            "--root",
            "benchmark",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
            "--bootstrap-seed",
            "7",
            "--bootstrap-resamples",
            "200",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(json_output.read_text())["protocol"] == REPEATED_ANALYSIS_PROTOCOL
    assert markdown_output.read_text().startswith("# Repeated benchmark analysis")
    assert json_output.stat().st_mode & 0o777 == 0o600
    assert markdown_output.stat().st_mode & 0o777 == 0o600
