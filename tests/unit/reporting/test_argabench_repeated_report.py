from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from arga_twins_benchmark.reporting.argabench_repeated_report import (
    ARGABENCH_REPEATED_PUBLICATION_MANIFEST_PROTOCOL,
    ARGABENCH_REPEATED_REPORT_PROTOCOL,
    ArgaBenchRepeatedReportError,
    build_argabench_repeated_report,
    write_argabench_repeated_report,
)
from arga_twins_benchmark.reporting.argabench_semantic_report import (
    ARGABENCH_PUBLICATION_MANIFEST_PROTOCOL,
    ARGABENCH_SEMANTIC_REPORT_PROTOCOL,
)


def _reports(task_count: int = 40) -> dict[int, dict[str, Any]]:
    reports: dict[int, dict[str, Any]] = {}
    profiles: dict[str, dict[str, Any]] = {
        f"profile-{profile_index:02}": {
            "profile": {
                "id": f"profile-{profile_index:02}",
                "label": f"Model {profile_index} High",
                "provider": "anthropic",
                "model_id": f"model-{profile_index:02}",
                "requested_effort": "high",
                "api_effort": "high",
                "thinking": "adaptive",
            },
            "publication_profile_id": f"model-{profile_index:02}@high",
            "scoring_ready": True,
        }
        for profile_index in range(37)
    }
    for repeat in (1, 2, 3):
        attempts: list[dict[str, Any]] = []
        for profile_index, profile_id in enumerate(profiles):
            for task_index in range(task_count):
                outcome = "pass" if (profile_index + task_index + repeat) % 3 == 0 else "fail"
                if profile_index == 0 and task_index == 0 and repeat == 3:
                    outcome = "unsafe"
                attempts.append(
                    {
                        "profile_id": profile_id,
                        "task_id": f"IT-{task_index + 1:02}",
                        "title": f"Task {task_index + 1}",
                        "domain": "it_support",
                        "prompt": f"Prompt {task_index + 1}",
                        "validity": "valid",
                        "score_eligible": True,
                        "semantic_outcome": outcome,
                        "cleanup_succeeded": True,
                        "metric_gaps": [],
                        "metrics": {
                            "input_tokens": 100,
                            "output_tokens": 10,
                            "estimated_cost_usd": 0.25,
                            "tool_calls": 6,
                            "provider_tool_calls": 5,
                            "official_docs_tool_calls": 1,
                        },
                        "run_id": f"run-{repeat}-{profile_index}-{task_index}",
                        "scenario_id": f"scenario-{task_index}",
                        "scenario_content_sha256": f"{task_index:064x}",
                        "scenario_execution_sha256": f"{task_index + 100:064x}",
                    }
                )
        reports[repeat] = {
            "protocol": ARGABENCH_SEMANTIC_REPORT_PROTOCOL,
            "suite_id": "argabench-40-v1",
            "task_ids": [f"IT-{task_index + 1:02}" for task_index in range(task_count)],
            "task_count": task_count,
            "matrix_scoring_ready": True,
            "scoring_ready_profile_count": 37,
            "source_matrix_dir": f"/preserved/repeat-{repeat}",
            "source_sha256": {
                "suite": "suite-hash",
                "tasks_md": "tasks-hash",
                "model_matrix": "profiles-hash",
                "historical_calibration": "calibration-hash",
            },
            "grader_provenance": {
                "method": "test_bundle",
                "bundle_sha256": "a" * 64,
                "source_files": {"reporting/test.py": "b" * 64},
            },
            "profiles": profiles,
            "attempts": attempts,
        }
    return reports


def test_builds_three_repeat_task_cluster_report() -> None:
    report = build_argabench_repeated_report(
        _reports(),
        bootstrap_seed=17,
        bootstrap_resamples=200,
    )

    assert report["protocol"] == ARGABENCH_REPEATED_REPORT_PROTOCOL
    assert report["scheduled_trials"] == 4440
    assert report["all_trials_scoring_ready"] is True
    assert report["semantic"]["denominator"] == 4440
    assert report["usage"] == {
        "input_tokens": 444_000,
        "output_tokens": 44_400,
        "estimated_cost_usd": 1110.0,
        "tool_calls": 26_640,
        "provider_tool_calls": 22_200,
        "official_docs_tool_calls": 4_440,
    }
    profile = report["profiles"]["profile-00"]
    assert profile["scheduled_trials"] == 120
    assert len(profile["task_clusters"]) == 40
    assert len(profile["by_repeat"]) == 3
    assert profile["uncertainty"] == {
        "method": "task_cluster_percentile_bootstrap",
        "confidence_level": 0.95,
        "cluster_definition": "task with all three repeats retained",
        "seed": 17,
        "resamples": 200,
        "ci_95": profile["uncertainty"]["ci_95"],
    }
    low, high = profile["uncertainty"]["ci_95"]
    assert 0 <= low <= profile["semantic"]["pass_rate"] <= high <= 1


def test_builds_selected_task_repeat_report() -> None:
    report = build_argabench_repeated_report(
        _reports(task_count=2),
        bootstrap_seed=17,
        bootstrap_resamples=20,
    )

    assert report["task_ids"] == ["IT-01", "IT-02"]
    assert report["task_count"] == 2
    assert report["scheduled_trials"] == 222
    assert report["profiles"]["profile-00"]["scheduled_trials"] == 6


def test_rejects_a_reused_candidate_run() -> None:
    reports = _reports()
    repeat_two = reports[2]["attempts"][0]
    repeat_two["run_id"] = reports[1]["attempts"][0]["run_id"]

    with pytest.raises(ArgaBenchRepeatedReportError, match="reused a candidate run"):
        build_argabench_repeated_report(reports, bootstrap_resamples=10)


def test_rejects_an_excluded_trial() -> None:
    reports = _reports()
    reports[3]["attempts"][0]["validity"] = "invalid_infrastructure"
    reports[3]["attempts"][0]["score_eligible"] = False

    with pytest.raises(ArgaBenchRepeatedReportError, match="excluded trial"):
        build_argabench_repeated_report(reports, bootstrap_resamples=10)


def test_rejects_a_changed_grader_revision() -> None:
    reports = _reports()
    reports[2]["grader_provenance"]["bundle_sha256"] = "c" * 64

    with pytest.raises(ArgaBenchRepeatedReportError, match="changed the executable grader"):
        build_argabench_repeated_report(reports, bootstrap_resamples=10)


def test_accepts_recreated_scenario_id_with_identical_content() -> None:
    reports = _reports()
    reports[2]["attempts"][0]["scenario_id"] = "recreated-scenario"
    reports[2]["attempts"][0]["scenario_content_sha256"] = "e" * 64

    report = build_argabench_repeated_report(reports, bootstrap_resamples=10)

    assert report["all_trials_scoring_ready"] is True


def test_rejects_changed_scenario_content() -> None:
    reports = _reports()
    reports[2]["attempts"][0]["scenario_id"] = "recreated-scenario"
    reports[2]["attempts"][0]["scenario_execution_sha256"] = "f" * 64

    with pytest.raises(
        ArgaBenchRepeatedReportError,
        match="changed candidate-visible scenario execution content",
    ):
        build_argabench_repeated_report(reports, bootstrap_resamples=10)


def test_writes_manifest_with_hashed_repeat_sources(tmp_path: Path) -> None:
    reports = _reports()
    paths: dict[int, Path] = {}
    for repeat, report in reports.items():
        repeat_dir = tmp_path / f"repeat-{repeat}-report"
        repeat_dir.mkdir()
        semantic_path = repeat_dir / "semantic-report.json"
        semantic_path.write_text(json.dumps(report))
        (repeat_dir / "publication-manifest.json").write_text(
            json.dumps(
                {
                    "protocol": ARGABENCH_PUBLICATION_MANIFEST_PROTOCOL,
                    "profiles": [{} for _ in range(37)],
                }
            )
        )
        paths[repeat] = semantic_path
    repeated = build_argabench_repeated_report(reports, bootstrap_resamples=10)

    outputs = write_argabench_repeated_report(
        repeated,
        tmp_path / "published",
        repeat_semantic_reports=paths,
        published_at="2026-08-17",
    )

    manifest = json.loads(Path(outputs["publication_manifest"]).read_text())
    assert manifest["protocol"] == ARGABENCH_REPEATED_PUBLICATION_MANIFEST_PROTOCOL
    assert manifest["repeat_count"] == 3
    assert manifest["grader_bundle_sha256"] == "a" * 64
    assert [source["repeat"] for source in manifest["sources"]] == [1, 2, 3]
    assert all(len(source["semantic_report_sha256"]) == 64 for source in manifest["sources"])
