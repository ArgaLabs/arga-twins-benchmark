"""Combine disjoint evidence-only regrades into one publication correction."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

PROTOCOL = "argabench-full-verifier-regrade/1"


def _read_object(path: Path) -> dict[str, Any]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return cast(dict[str, Any], payload)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: object) -> str:
    source = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(source).hexdigest()


def _count(items: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts = Counter(str(item.get(field)) for item in items)
    return {outcome: counts[outcome] for outcome in ("pass", "fail", "unsafe")}


def _group(items: Sequence[Mapping[str, Any]], key: str, field: str) -> dict[str, dict[str, int]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in items:
        grouped[str(item[key])].append(item)
    return {group: _count(values, field) for group, values in sorted(grouped.items())}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_paths = cast(list[Path], args.report)
    if len(report_paths) < 2:
        raise ValueError("at least two component reports are required")
    reports = [_read_object(path) for path in report_paths]
    if any(report.get("protocol") != PROTOCOL for report in reports):
        raise ValueError("all component reports must use the executable regrade protocol")

    suite_hashes = {report["source"]["suiteSha256"] for report in reports}
    prompt_hashes = {report["source"]["tasksMarkdownSha256"] for report in reports}
    policies = {json.dumps(report["classificationPolicy"], sort_keys=True) for report in reports}
    if len(suite_hashes) != 1 or len(prompt_hashes) != 1 or len(policies) != 1:
        raise ValueError("component reports use different suite, prompt, or classification contracts")

    trials = [dict(trial) for report in reports for trial in cast(list[dict[str, Any]], report["trials"])]
    trial_ids = [trial.get("trialId") for trial in trials]
    run_ids = [trial.get("runId") for trial in trials]
    if len(set(trial_ids)) != len(trial_ids) or len(set(run_ids)) != len(run_ids):
        raise ValueError("component reports overlap by trial ID or run ID")

    transitions = Counter(f"{trial['sourceOutcome']}->{trial['semanticOutcome']}" for trial in trials)
    verdict_changes = [
        {
            "trialId": trial["trialId"],
            "runId": trial["runId"],
            "taskId": trial["taskId"],
            "profileId": trial["profileId"],
            "repeat": trial["repeat"],
            "sourceOutcome": trial["sourceOutcome"],
            "regradedOutcome": trial["semanticOutcome"],
        }
        for trial in trials
        if trial["sourceOutcome"] != trial["semanticOutcome"]
    ]
    component_graders = [
        {
            "report": path.name,
            "reportSha256": _sha256_file(path),
            "graderBundleSha256": report["grader"]["bundle_sha256"],
            "trialCount": report["trialCount"],
        }
        for path, report in zip(report_paths, reports, strict=True)
    ]
    source = dict(reports[-1]["source"])
    source["componentReports"] = component_graders
    report = {
        "protocol": PROTOCOL,
        "regradedAt": max(str(item["regradedAt"]) for item in reports),
        "method": (
            "combined offline executable verifier results over exact saved baseline state, "
            "final state, and mediated tool traces"
        ),
        "scope": {
            "taskIds": sorted({str(trial["taskId"]) for trial in trials}),
            "profileIds": sorted({str(trial["profileId"]) for trial in trials}),
            "trialCount": len(trials),
        },
        "classificationPolicy": reports[-1]["classificationPolicy"],
        "source": source,
        "grader": {
            "bundle_sha256": _canonical_sha256(component_graders),
            "components": component_graders,
        },
        "trialCount": len(trials),
        "outcomesBefore": _count(trials, "sourceOutcome"),
        "outcomesAfter": _count(trials, "semanticOutcome"),
        "transitions": dict(sorted(transitions.items())),
        "taskOutcomesBefore": _group(trials, "taskId", "sourceOutcome"),
        "taskOutcomesAfter": _group(trials, "taskId", "semanticOutcome"),
        "profileOutcomesBefore": _group(trials, "profileId", "sourceOutcome"),
        "profileOutcomesAfter": _group(trials, "profileId", "semanticOutcome"),
        "verdictChangeCount": len(verdict_changes),
        "verdictChanges": verdict_changes,
        "trials": trials,
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
                "graderBundleSha256": report["grader"]["bundle_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
