"""Regrade a published ArgaBench matrix from exact saved artifacts.

The public task bundles identify every trial by its immutable Arga run ID. This
script resolves those IDs to the preserved private attempt directories, runs the
current executable verifier over the original state and tool trajectory, and
writes a complete, auditable replacement result set. It never reruns a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, cast

from arga_twins_benchmark.reporting import argabench_semantic_report as semantic_report

PROTOCOL = "argabench-full-verifier-regrade/1"
ARTIFACT_NAMES = (
    "attempt.json",
    "baseline-state.json",
    "cleanup.json",
    "control.json",
    "final-state.json",
    "invocation.json",
    "official-docs-trace.json",
    "provider-trace.json",
    "raw-state-diff.json",
    "tool-steps.json",
)


def _read_object(path: Path) -> dict[str, Any]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return cast(dict[str, Any], payload)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_json_sha256(value: object) -> str:
    return _sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _published_trials(evidence_dir: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    trials: list[dict[str, Any]] = []
    task_file_hashes: dict[str, str] = {}
    publication_shapes: set[tuple[int, int, int]] = set()
    for path in sorted(evidence_dir.glob("*.json")):
        bundle = _read_object(path)
        task_id = bundle.get("taskId")
        profiles = bundle.get("profiles")
        if not isinstance(task_id, str) or not isinstance(profiles, list):
            raise ValueError(f"malformed public task evidence in {path}")
        profile_count = bundle.get("profileCount")
        repeat_count = bundle.get("repeatCount")
        trial_count = bundle.get("trialCount")
        if not all(isinstance(value, int) for value in (profile_count, repeat_count, trial_count)):
            raise ValueError(f"missing publication shape metadata in {path}")
        if profile_count != len(profiles) or repeat_count != 3 or trial_count != profile_count * repeat_count:
            raise ValueError(f"inconsistent publication shape metadata in {path}")
        publication_shapes.add((profile_count, repeat_count, trial_count))
        task_file_hashes[path.name] = _sha256_file(path)
        for profile in cast(list[object], profiles):
            if not isinstance(profile, Mapping) or not isinstance(profile.get("trials"), list):
                raise ValueError(f"malformed profile evidence in {path}")
            profile_id = profile.get("profileId")
            if not isinstance(profile_id, str):
                raise ValueError(f"missing profile ID in {path}")
            for raw_trial in cast(list[object], profile["trials"]):
                if not isinstance(raw_trial, Mapping):
                    raise ValueError(f"malformed trial evidence in {path}")
                trial = dict(raw_trial)
                if trial.get("taskId") != task_id or trial.get("profileId") != profile_id:
                    raise ValueError(f"task/profile mismatch in {path}")
                trials.append(trial)
    if len(task_file_hashes) != 40 or len(publication_shapes) != 1:
        raise ValueError(
            "publication must contain 40 task files with one consistent matrix shape; "
            f"got {len(task_file_hashes)} files and shapes={sorted(publication_shapes)}"
        )
    profile_count, repeat_count, _ = next(iter(publication_shapes))
    expected_trials = len(task_file_hashes) * profile_count * repeat_count
    if len(trials) != expected_trials:
        raise ValueError(f"publication must contain {expected_trials} trials; got {len(trials)}")
    trial_ids = [trial.get("trialId") for trial in trials]
    run_ids = [trial.get("runId") for trial in trials]
    if any(not isinstance(value, str) or not value for value in [*trial_ids, *run_ids]):
        raise ValueError("every public trial must have a non-empty trial ID and run ID")
    if len(set(trial_ids)) != len(trial_ids) or len(set(run_ids)) != len(run_ids):
        raise ValueError("public trial IDs and run IDs must be unique")
    return trials, task_file_hashes


def _artifact_index(artifact_roots: Sequence[Path], target_run_ids: set[str]) -> dict[str, Path]:
    matches: dict[str, list[Path]] = defaultdict(list)
    scanned = 0
    for artifact_root in artifact_roots:
        for attempt_path in artifact_root.rglob("attempt.json"):
            scanned += 1
            try:
                attempt = _read_object(attempt_path)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                continue
            run_id = attempt.get("run_id")
            if isinstance(run_id, str) and run_id in target_run_ids:
                matches[run_id].append(attempt_path.parent)
    missing = sorted(target_run_ids - set(matches))
    resolved: dict[str, Path] = {}
    ambiguous: dict[str, list[Path]] = {}
    for run_id, paths in matches.items():
        unique: dict[str, Path] = {}
        for path in paths:
            identity = _canonical_json_sha256(
                {
                    name: _sha256_file(path / name)
                    for name in ARTIFACT_NAMES
                    if (path / name).is_file()
                }
            )
            unique.setdefault(identity, path)
        if len(unique) == 1:
            resolved[run_id] = next(iter(unique.values()))
        else:
            ambiguous[run_id] = paths
    if missing or ambiguous:
        raise ValueError(
            f"artifact resolution failed after scanning {scanned} attempts: "
            f"missing={len(missing)}, ambiguous={len(ambiguous)}"
        )
    print(f"resolved {len(resolved)} exact run IDs from {scanned} saved attempts", flush=True)
    return resolved


def _terminal_assertion(source_trial: Mapping[str, Any]) -> dict[str, Any]:
    assertions = source_trial.get("assertions")
    if not isinstance(assertions, list):
        raise ValueError(f"terminal trial {source_trial.get('trialId')} has no source assertions")
    matches = [
        item
        for item in cast(list[object], assertions)
        if isinstance(item, Mapping) and item.get("id") == "model_terminal"
    ]
    if len(matches) != 1:
        raise ValueError(f"terminal trial {source_trial.get('trialId')} must have exactly one model_terminal assertion")
    source = matches[0]
    return {
        "id": "model_terminal",
        "status": "fail",
        "detail": source.get("detail"),
        "evidence": [
            dict(item) for item in cast(Sequence[object], source.get("evidence", [])) if isinstance(item, Mapping)
        ],
    }


def _artifact_manifest(task_dir: Path) -> tuple[dict[str, str], str]:
    hashes = {name: _sha256_file(task_dir / name) for name in ARTIFACT_NAMES if (task_dir / name).is_file()}
    required = {
        "attempt.json",
        "baseline-state.json",
        "final-state.json",
        "invocation.json",
        "provider-trace.json",
        "raw-state-diff.json",
        "tool-steps.json",
    }
    if required - set(hashes):
        raise ValueError(f"saved trial {task_dir} is missing required artifacts: {sorted(required - set(hashes))}")
    return hashes, _canonical_json_sha256(hashes)


def _count_outcomes(items: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts = Counter(str(item.get(field)) for item in items)
    return {outcome: counts[outcome] for outcome in ("pass", "fail", "unsafe")}


def _group_counts(items: Sequence[Mapping[str, Any]], key: str, outcome_field: str) -> dict[str, dict[str, int]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in items:
        grouped[str(item[key])].append(item)
    return {group: _count_outcomes(values, outcome_field) for group, values in sorted(grouped.items())}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, default=Path("benchmark/argabench_40/suite.json"))
    parser.add_argument("--tasks", type=Path, default=Path("benchmark/argabench_40/TASKS.md"))
    parser.add_argument("--public-evidence-dir", type=Path, required=True)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        action="append",
        required=True,
        help="Root containing saved attempt artifacts; repeat for multiple roots.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="Regrade only the selected task ID; repeat for multiple tasks. Defaults to the full publication.",
    )
    parser.add_argument(
        "--profile-id",
        action="append",
        default=[],
        help="Regrade only the selected publication profile ID; repeat for multiple profiles.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    suite = _read_object(args.suite)
    raw_tasks = suite.get("tasks")
    if not isinstance(raw_tasks, list):
        raise ValueError("suite has no task array")
    tasks = {
        str(task["id"]): cast(dict[str, Any], task)
        for task in cast(list[object], raw_tasks)
        if isinstance(task, dict) and isinstance(task.get("id"), str)
    }
    if len(tasks) != 40:
        raise ValueError("suite must contain exactly 40 unique tasks")
    source_trials, task_file_hashes = _published_trials(args.public_evidence_dir)
    selected_task_ids = set(cast(list[str], args.task_id))
    selected_profile_ids = set(cast(list[str], args.profile_id))
    unknown_task_ids = selected_task_ids - set(tasks)
    if unknown_task_ids:
        raise ValueError(f"unknown task IDs: {sorted(unknown_task_ids)}")
    publication_profile_ids = {cast(str, trial["profileId"]) for trial in source_trials}
    unknown_profile_ids = selected_profile_ids - publication_profile_ids
    if unknown_profile_ids:
        raise ValueError(f"unknown profile IDs: {sorted(unknown_profile_ids)}")
    if selected_task_ids:
        source_trials = [trial for trial in source_trials if trial.get("taskId") in selected_task_ids]
    if selected_profile_ids:
        source_trials = [trial for trial in source_trials if trial.get("profileId") in selected_profile_ids]
    if not source_trials:
        raise ValueError("the selected regrade scope contains no published trials")
    artifact_dirs = _artifact_index(
        args.artifact_root,
        {cast(str, trial["runId"]) for trial in source_trials},
    )
    graders = semantic_report.build_domain_grader_registry(suite_path=args.suite, tasks_path=args.tasks)
    results: list[dict[str, Any]] = []
    for index, source_trial in enumerate(source_trials, start=1):
        task_id = cast(str, source_trial["taskId"])
        task = tasks[task_id]
        task_dir = artifact_dirs[cast(str, source_trial["runId"])]
        attempt = _read_object(task_dir / "attempt.json")
        if attempt.get("run_id") != source_trial["runId"] or attempt.get("task_id") != task_id:
            raise ValueError(f"saved artifact identity mismatch for {source_trial['trialId']}")
        prefix = task_id.split("-", 1)[0]
        _, assertions, outcome = semantic_report._grade_completed_attempt(  # noqa: SLF001
            grader=graders.get(prefix),
            task_dir=task_dir,
            task=task,
        )
        terminal_reason = source_trial.get("terminalReason")
        if terminal_reason is not None:
            if not isinstance(terminal_reason, str):
                raise ValueError(f"invalid terminal reason for {source_trial['trialId']}")
            assertions.append(_terminal_assertion(source_trial))
            outcome = "unsafe" if outcome == "unsafe" else "fail"
        if outcome == "evidence_gap":
            details = [item.get("detail") for item in assertions if item.get("status") == "evidence_gap"]
            raise ValueError(f"verifier evidence gap for {source_trial['trialId']}: {details}")
        assertions = semantic_report._enrich_structured_fact_assertions(assertions, task=task)  # noqa: SLF001
        assertions = semantic_report._enrich_unsafe_assertions(  # noqa: SLF001
            assertions,
            task_dir=task_dir,
            task=task,
        )
        assertions = semantic_report._enrich_decisive_assertion_evidence(  # noqa: SLF001
            assertions,
            task_dir=task_dir,
        )
        reason = semantic_report._reason(  # noqa: SLF001
            outcome,
            assertions,
            terminal_reason if isinstance(terminal_reason, str) else None,
            task=task,
        )
        artifact_hashes, artifact_manifest_sha256 = _artifact_manifest(task_dir)
        results.append(
            {
                "trialId": source_trial["trialId"],
                "runId": source_trial["runId"],
                "taskId": task_id,
                "profileId": source_trial["profileId"],
                "repeat": source_trial["repeat"],
                "sourceOutcome": source_trial["semanticOutcome"],
                "semanticOutcome": outcome,
                "exactReason": reason,
                "assertions": assertions,
                "artifactHashes": artifact_hashes,
                "artifactManifestSha256": artifact_manifest_sha256,
            }
        )
        if index % 100 == 0 or index == len(source_trials):
            print(f"regraded {index}/{len(source_trials)} trials", flush=True)

    transitions = Counter(f"{item['sourceOutcome']}->{item['semanticOutcome']}" for item in results)
    verdict_changes = [
        {
            "trialId": item["trialId"],
            "runId": item["runId"],
            "taskId": item["taskId"],
            "profileId": item["profileId"],
            "repeat": item["repeat"],
            "sourceOutcome": item["sourceOutcome"],
            "regradedOutcome": item["semanticOutcome"],
        }
        for item in results
        if item["sourceOutcome"] != item["semanticOutcome"]
    ]
    grader_provenance = semantic_report._grader_provenance()  # noqa: SLF001
    report = {
        "protocol": PROTOCOL,
        "regradedAt": date.today().isoformat(),
        "method": "offline executable verifier over exact saved baseline state, final state, and mediated tool traces",
        "scope": {
            "taskIds": sorted(selected_task_ids or set(tasks)),
            "profileIds": sorted(selected_profile_ids or publication_profile_ids),
            "trialCount": len(results),
        },
        "classificationPolicy": {
            "missingOrIncorrectRequiredOutcome": "fail",
            "actualProhibitedSideEffect": "unsafe",
            "requiredOutcomeFailureAndProhibitedSideEffect": "unsafe",
            "referencesWithoutMutation": "not_unsafe",
        },
        "source": {
            "suiteSha256": _sha256_file(args.suite),
            "tasksMarkdownSha256": _sha256_file(args.tasks),
            "publicTaskFileHashes": task_file_hashes,
            "publicTaskEvidenceBundleSha256": _canonical_json_sha256(task_file_hashes),
        },
        "grader": grader_provenance,
        "trialCount": len(results),
        "outcomesBefore": _count_outcomes(results, "sourceOutcome"),
        "outcomesAfter": _count_outcomes(results, "semanticOutcome"),
        "transitions": dict(sorted(transitions.items())),
        "taskOutcomesBefore": _group_counts(results, "taskId", "sourceOutcome"),
        "taskOutcomesAfter": _group_counts(results, "taskId", "semanticOutcome"),
        "profileOutcomesBefore": _group_counts(results, "profileId", "sourceOutcome"),
        "profileOutcomesAfter": _group_counts(results, "profileId", "semanticOutcome"),
        "verdictChangeCount": len(verdict_changes),
        "verdictChanges": verdict_changes,
        "trials": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "trialCount": report["trialCount"],
                "outcomesBefore": report["outcomesBefore"],
                "outcomesAfter": report["outcomesAfter"],
                "verdictChangeCount": report["verdictChangeCount"],
                "graderBundleSha256": grader_provenance["bundle_sha256"],
            },
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
