from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, cast

from arga_twins_benchmark.lifecycle import write_private_json
from arga_twins_benchmark.reporting.cross_functional_semantic_report import (
    CROSS_FUNCTIONAL_PUBLICATION_MANIFEST_PROTOCOL,
    CROSS_FUNCTIONAL_SEMANTIC_REPORT_PROTOCOL,
)

CROSS_FUNCTIONAL_REPEATED_REPORT_PROTOCOL = "arga-bench-cross-functional-repeated-semantic-report/1"
CROSS_FUNCTIONAL_REPEATED_PUBLICATION_MANIFEST_PROTOCOL = "arga-bench-cross-functional-repeated-publication-manifest/1"

_EXPECTED_PROFILE_COUNT = 32
_EXPECTED_REPEATS = (1, 2, 3)
_SEMANTIC_OUTCOMES = frozenset({"pass", "fail", "unsafe"})
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "estimated_cost_usd",
    "tool_calls",
    "provider_tool_calls",
    "official_docs_tool_calls",
)


class CrossFunctionalRepeatedReportError(ValueError):
    """Raised when repeat artifacts cannot support a comparable three-repeat report."""


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise CrossFunctionalRepeatedReportError("cannot calculate a percentile without samples")
    position = probability * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def _bootstrap_task_cluster_ci(
    pass_rates_by_task: Sequence[float],
    *,
    seed: int,
    resamples: int,
) -> list[float]:
    task_count = len(pass_rates_by_task)
    if task_count < 1:
        raise CrossFunctionalRepeatedReportError("bootstrap requires at least one task cluster")
    if resamples < 1:
        raise CrossFunctionalRepeatedReportError("bootstrap resamples must be positive")
    rng = random.Random(seed)
    sampled = sorted(
        sum(pass_rates_by_task[rng.randrange(task_count)] for _ in range(task_count)) / task_count
        for _ in range(resamples)
    )
    return [_percentile(sampled, 0.025), _percentile(sampled, 0.975)]


def _usage(attempts: Sequence[Mapping[str, Any]]) -> dict[str, int | float]:
    totals: dict[str, int | float] = {}
    for field in _USAGE_FIELDS:
        values = [cast(Mapping[str, Any], item.get("metrics", {})).get(field) for item in attempts]
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            raise CrossFunctionalRepeatedReportError(f"every repeated trial requires metric {field}")
        total = sum(cast(Sequence[int | float], values))
        totals[field] = round(float(total), 8) if field == "estimated_cost_usd" else int(total)
    return totals


def _semantic(attempts: Sequence[Mapping[str, Any]]) -> dict[str, int | float]:
    outcomes = Counter(cast(str, item["semantic_outcome"]) for item in attempts)
    denominator = len(attempts)
    return {
        "denominator": denominator,
        "pass": outcomes["pass"],
        "fail": outcomes["fail"],
        "unsafe": outcomes["unsafe"],
        "pass_rate": outcomes["pass"] / denominator,
    }


def _validate_report(
    report: Mapping[str, Any],
    *,
    repeat: int,
    expected_hashes: Mapping[str, Any] | None,
    expected_grader_provenance: Mapping[str, Any] | None,
    expected_task_ids: Sequence[str] | None,
) -> tuple[
    dict[str, Mapping[str, Any]],
    list[Mapping[str, Any]],
    Mapping[str, Any],
    tuple[str, ...],
]:
    if report.get("protocol") != CROSS_FUNCTIONAL_SEMANTIC_REPORT_PROTOCOL:
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} has an unsupported report protocol")
    if report.get("suite_id") != "cross-functional-40-v1":
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} changed suite identity")
    if report.get("matrix_scoring_ready") is not True:
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} is not matrix-scoring-ready")
    if report.get("scoring_ready_profile_count") != _EXPECTED_PROFILE_COUNT:
        raise CrossFunctionalRepeatedReportError(
            f"repeat {repeat} must contain {_EXPECTED_PROFILE_COUNT} scoring-ready profiles"
        )
    source_hashes = report.get("source_sha256")
    if not isinstance(source_hashes, Mapping):
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} is missing source hashes")
    typed_source_hashes = cast(Mapping[str, Any], source_hashes)
    if expected_hashes is not None and dict(typed_source_hashes) != dict(expected_hashes):
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} changed the suite, task, or profile inputs")
    grader_provenance = report.get("grader_provenance")
    if not isinstance(grader_provenance, Mapping):
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} is missing executable grader provenance")
    typed_grader_provenance = cast(Mapping[str, Any], grader_provenance)
    bundle_sha256 = typed_grader_provenance.get("bundle_sha256")
    if not isinstance(bundle_sha256, str) or len(bundle_sha256) != 64:
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} has invalid grader provenance")
    if expected_grader_provenance is not None and dict(typed_grader_provenance) != dict(expected_grader_provenance):
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} changed the executable grader revision")

    raw_profiles = report.get("profiles")
    if not isinstance(raw_profiles, Mapping):
        raise CrossFunctionalRepeatedReportError(
            f"repeat {repeat} must contain exactly {_EXPECTED_PROFILE_COUNT} profiles"
        )
    typed_raw_profiles = cast(Mapping[object, object], raw_profiles)
    if len(typed_raw_profiles) != _EXPECTED_PROFILE_COUNT:
        raise CrossFunctionalRepeatedReportError(
            f"repeat {repeat} must contain exactly {_EXPECTED_PROFILE_COUNT} profiles"
        )
    profiles = {
        str(profile_id): cast(Mapping[str, Any], profile)
        for profile_id, profile in typed_raw_profiles.items()
        if isinstance(profile, Mapping)
    }
    if len(profiles) != _EXPECTED_PROFILE_COUNT:
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} contains a malformed profile")
    if any(profile.get("scoring_ready") is not True for profile in profiles.values()):
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} contains a non-scoring profile")

    raw_attempts = report.get("attempts")
    if not isinstance(raw_attempts, list):
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} must contain attempts")
    typed_raw_attempts = cast(list[object], raw_attempts)
    attempts = [cast(Mapping[str, Any], item) for item in typed_raw_attempts if isinstance(item, Mapping)]
    if len(attempts) != len(typed_raw_attempts) or not attempts:
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} contains a malformed attempt")
    observed_task_ids = sorted(
        {cast(str, item["task_id"]) for item in attempts if isinstance(item.get("task_id"), str)}
    )
    raw_task_ids = report.get("task_ids")
    if raw_task_ids is None:
        task_ids = tuple(observed_task_ids)
    elif isinstance(raw_task_ids, list) and raw_task_ids and all(isinstance(task_id, str) for task_id in raw_task_ids):
        task_ids = tuple(cast(list[str], raw_task_ids))
    else:
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} has invalid selected task ids")
    if (
        len(task_ids) != len(set(task_ids))
        or set(task_ids) != set(observed_task_ids)
        or (report.get("task_count") is not None and report.get("task_count") != len(task_ids))
    ):
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} changed its selected task set")
    if expected_task_ids is not None and tuple(expected_task_ids) != task_ids:
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} changed the selected task set")
    expected_attempts = _EXPECTED_PROFILE_COUNT * len(task_ids)
    if len(attempts) != expected_attempts:
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} must contain exactly {expected_attempts} attempts")
    identities = {(item.get("profile_id"), item.get("task_id")) for item in attempts}
    if len(identities) != expected_attempts:
        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} has duplicate profile/task slots")
    for item in attempts:
        if item.get("profile_id") not in profiles:
            raise CrossFunctionalRepeatedReportError(f"repeat {repeat} contains an unknown profile")
        if item.get("validity") != "valid" or item.get("score_eligible") is not True:
            raise CrossFunctionalRepeatedReportError(f"repeat {repeat} contains an excluded trial")
        if item.get("semantic_outcome") not in _SEMANTIC_OUTCOMES:
            raise CrossFunctionalRepeatedReportError(f"repeat {repeat} contains an invalid verdict")
        if item.get("cleanup_succeeded") is not True:
            raise CrossFunctionalRepeatedReportError(f"repeat {repeat} contains an unproven teardown")
        metrics = item.get("metrics")
        if not isinstance(metrics, Mapping) or item.get("metric_gaps"):
            raise CrossFunctionalRepeatedReportError(f"repeat {repeat} contains incomplete metrics")
    return profiles, attempts, typed_grader_provenance, task_ids


def build_cross_functional_repeated_report(
    reports: Mapping[int, Mapping[str, Any]],
    *,
    bootstrap_seed: int = 20260817,
    bootstrap_resamples: int = 10_000,
) -> dict[str, Any]:
    """Combine three scoring-ready semantic reports without weakening any trial verdict."""

    if tuple(sorted(reports)) != _EXPECTED_REPEATS:
        raise CrossFunctionalRepeatedReportError("exactly repeats 1, 2, and 3 are required")

    expected_hashes: Mapping[str, Any] | None = None
    expected_grader_provenance: Mapping[str, Any] | None = None
    reference_profiles: dict[str, Mapping[str, Any]] | None = None
    reference_task_ids: tuple[str, ...] | None = None
    repeat_attempts: dict[int, list[Mapping[str, Any]]] = {}
    repeat_summaries: list[dict[str, Any]] = []
    for repeat in _EXPECTED_REPEATS:
        report = reports[repeat]
        profiles, attempts, grader_provenance, task_ids = _validate_report(
            report,
            repeat=repeat,
            expected_hashes=expected_hashes,
            expected_grader_provenance=expected_grader_provenance,
            expected_task_ids=reference_task_ids,
        )
        hashes = cast(Mapping[str, Any], report["source_sha256"])
        expected_hashes = hashes if expected_hashes is None else expected_hashes
        expected_grader_provenance = (
            grader_provenance if expected_grader_provenance is None else expected_grader_provenance
        )
        if reference_profiles is None:
            reference_profiles = profiles
            reference_task_ids = task_ids
        else:
            for profile_id, profile in profiles.items():
                if profile_id not in reference_profiles:
                    raise CrossFunctionalRepeatedReportError(f"repeat {repeat} added profile {profile_id}")
                for field in ("profile", "publication_profile_id"):
                    if profile.get(field) != reference_profiles[profile_id].get(field):
                        raise CrossFunctionalRepeatedReportError(f"repeat {repeat} changed {profile_id}.{field}")
        repeat_attempts[repeat] = attempts
        repeat_summaries.append(
            {
                "repeat": repeat,
                "source_matrix_dir": report.get("source_matrix_dir"),
                "semantic": _semantic(attempts),
                "usage": _usage(attempts),
            }
        )

    assert reference_profiles is not None
    assert reference_task_ids is not None
    task_count = len(reference_task_ids)
    combined = [item for repeat in _EXPECTED_REPEATS for item in repeat_attempts[repeat]]
    profile_reports: dict[str, dict[str, Any]] = {}
    for profile_index, profile_id in enumerate(reference_profiles):
        profile_attempts = [item for item in combined if item.get("profile_id") == profile_id]
        expected_profile_trials = task_count * len(_EXPECTED_REPEATS)
        if len(profile_attempts) != expected_profile_trials:
            raise CrossFunctionalRepeatedReportError(
                f"profile {profile_id} does not have {expected_profile_trials} trials"
            )
        by_task: dict[str, list[Mapping[str, Any]]] = {}
        for item in profile_attempts:
            by_task.setdefault(cast(str, item["task_id"]), []).append(item)
        if (
            len(by_task) != task_count
            or set(by_task) != set(reference_task_ids)
            or any(len(task_attempts) != len(_EXPECTED_REPEATS) for task_attempts in by_task.values())
        ):
            raise CrossFunctionalRepeatedReportError(f"profile {profile_id} lacks three trials per task")

        task_clusters: list[dict[str, Any]] = []
        pass_rates: list[float] = []
        for task_id in sorted(by_task):
            task_attempts = by_task[task_id]
            run_ids = [item.get("run_id") for item in task_attempts]
            if not all(isinstance(run_id, str) and run_id for run_id in run_ids):
                raise CrossFunctionalRepeatedReportError(f"{profile_id}/{task_id} is missing a run ID")
            if len(set(run_ids)) != len(_EXPECTED_REPEATS):
                raise CrossFunctionalRepeatedReportError(f"{profile_id}/{task_id} reused a candidate run")
            scenario_ids = [item.get("scenario_id") for item in task_attempts]
            if not all(isinstance(scenario_id, str) and scenario_id for scenario_id in scenario_ids):
                raise CrossFunctionalRepeatedReportError(f"{profile_id}/{task_id} is missing a scenario ID")
            scenario_hashes = {item.get("scenario_execution_sha256") for item in task_attempts}
            if len(scenario_hashes) != 1 or not all(
                isinstance(digest, str)
                and len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest)
                for digest in scenario_hashes
            ):
                raise CrossFunctionalRepeatedReportError(
                    f"{profile_id}/{task_id} changed candidate-visible scenario execution content"
                )
            prompt_facts = {(item.get("title"), item.get("domain"), item.get("prompt")) for item in task_attempts}
            if len(prompt_facts) != 1:
                raise CrossFunctionalRepeatedReportError(f"{profile_id}/{task_id} changed task content")
            outcomes = [cast(str, item["semantic_outcome"]) for item in task_attempts]
            pass_rate = sum(outcome == "pass" for outcome in outcomes) / len(outcomes)
            pass_rates.append(pass_rate)
            task_clusters.append(
                {
                    "task_id": task_id,
                    "scenario_execution_sha256": next(iter(scenario_hashes)),
                    "pass_rate": pass_rate,
                    "exact_outcome_consistent": len(set(outcomes)) == 1,
                    "outcomes_by_repeat": [
                        {"repeat": repeat, "outcome": outcomes[index]} for index, repeat in enumerate(_EXPECTED_REPEATS)
                    ],
                }
            )

        semantic = _semantic(profile_attempts)
        profile_reports[profile_id] = {
            "profile": reference_profiles[profile_id]["profile"],
            "publication_profile_id": reference_profiles[profile_id]["publication_profile_id"],
            "scheduled_trials": len(profile_attempts),
            "semantic": semantic,
            "usage": _usage(profile_attempts),
            "by_repeat": [
                {
                    "repeat": repeat,
                    "semantic": _semantic(
                        [item for item in repeat_attempts[repeat] if item.get("profile_id") == profile_id]
                    ),
                    "usage": _usage([item for item in repeat_attempts[repeat] if item.get("profile_id") == profile_id]),
                }
                for repeat in _EXPECTED_REPEATS
            ],
            "task_clusters": task_clusters,
            "repeat_consistency": {
                "exact_outcome_consistent_tasks": sum(
                    cluster["exact_outcome_consistent"] is True for cluster in task_clusters
                ),
                "mixed_outcome_tasks": sum(cluster["exact_outcome_consistent"] is False for cluster in task_clusters),
            },
            "uncertainty": {
                "method": "task_cluster_percentile_bootstrap",
                "confidence_level": 0.95,
                "cluster_definition": "task with all three repeats retained",
                "seed": bootstrap_seed,
                "resamples": bootstrap_resamples,
                "ci_95": _bootstrap_task_cluster_ci(
                    pass_rates,
                    seed=bootstrap_seed + profile_index,
                    resamples=bootstrap_resamples,
                ),
            },
        }

    return {
        "protocol": CROSS_FUNCTIONAL_REPEATED_REPORT_PROTOCOL,
        "suite_id": "cross-functional-40-v1",
        "repeat_count": len(_EXPECTED_REPEATS),
        "repeats": list(_EXPECTED_REPEATS),
        "profile_count": len(profile_reports),
        "task_ids": list(reference_task_ids),
        "task_count": task_count,
        "scheduled_trials": len(combined),
        "all_trials_scoring_ready": True,
        "source_sha256": dict(expected_hashes or {}),
        "grader_provenance": dict(expected_grader_provenance or {}),
        "semantic": _semantic(combined),
        "usage": _usage(combined),
        "repeat_summaries": repeat_summaries,
        "profiles": profile_reports,
    }


def write_cross_functional_repeated_report(
    report: Mapping[str, Any],
    output_dir: Path,
    *,
    repeat_semantic_reports: Mapping[int, Path],
    published_at: str | None = None,
) -> dict[str, str]:
    """Write a repeat manifest that keeps each trial tied to its preserved source matrix."""

    if report.get("protocol") != CROSS_FUNCTIONAL_REPEATED_REPORT_PROTOCOL:
        raise CrossFunctionalRepeatedReportError("unsupported repeated report protocol")
    if report.get("all_trials_scoring_ready") is not True:
        raise CrossFunctionalRepeatedReportError("refusing to publish incomplete repeat evidence")
    if tuple(sorted(repeat_semantic_reports)) != _EXPECTED_REPEATS:
        raise CrossFunctionalRepeatedReportError("publication requires three semantic report paths")
    publication_date = published_at or date.today().isoformat()
    if not publication_date or len(publication_date) != 10:
        raise CrossFunctionalRepeatedReportError("published_at must use YYYY-MM-DD")

    output = output_dir.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise CrossFunctionalRepeatedReportError("output directory must be absent or empty")
    sources: list[dict[str, Any]] = []
    for repeat in _EXPECTED_REPEATS:
        semantic_path = repeat_semantic_reports[repeat].resolve()
        publication_path = semantic_path.with_name("publication-manifest.json")
        if not semantic_path.is_file() or not publication_path.is_file():
            raise CrossFunctionalRepeatedReportError(
                f"repeat {repeat} requires semantic-report.json and publication-manifest.json"
            )
        semantic = json.loads(semantic_path.read_text())
        publication = json.loads(publication_path.read_text())
        if semantic.get("protocol") != CROSS_FUNCTIONAL_SEMANTIC_REPORT_PROTOCOL:
            raise CrossFunctionalRepeatedReportError(f"repeat {repeat} semantic report changed")
        if publication.get("protocol") != CROSS_FUNCTIONAL_PUBLICATION_MANIFEST_PROTOCOL:
            raise CrossFunctionalRepeatedReportError(f"repeat {repeat} publication manifest changed")
        if len(publication.get("profiles", [])) != _EXPECTED_PROFILE_COUNT:
            raise CrossFunctionalRepeatedReportError(f"repeat {repeat} publication is incomplete")
        sources.append(
            {
                "repeat": repeat,
                "semantic_report": str(semantic_path),
                "publication_manifest": str(publication_path),
                "semantic_report_sha256": _sha256_path(semantic_path),
                "publication_manifest_sha256": _sha256_path(publication_path),
            }
        )

    manifest = {
        "protocol": CROSS_FUNCTIONAL_REPEATED_PUBLICATION_MANIFEST_PROTOCOL,
        "suite_id": report["suite_id"],
        "published_at": publication_date,
        "repeat_count": len(_EXPECTED_REPEATS),
        "grader_bundle_sha256": cast(Mapping[str, Any], report["grader_provenance"])["bundle_sha256"],
        "aggregate_report": "repeated-semantic-report.json",
        "sources": sources,
    }
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    report_path = output / "repeated-semantic-report.json"
    manifest_path = output / "publication-manifest.json"
    write_private_json(report_path, dict(report))
    write_private_json(manifest_path, manifest)
    return {"repeated_report": str(report_path), "publication_manifest": str(manifest_path)}


__all__ = [
    "CROSS_FUNCTIONAL_REPEATED_PUBLICATION_MANIFEST_PROTOCOL",
    "CROSS_FUNCTIONAL_REPEATED_REPORT_PROTOCOL",
    "CrossFunctionalRepeatedReportError",
    "build_cross_functional_repeated_report",
    "write_cross_functional_repeated_report",
]
