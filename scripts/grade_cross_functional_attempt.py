#!/usr/bin/env python3
"""Grade one preserved Cross-Functional 40 attempt without rerunning a model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from arga_twins_benchmark.lifecycle import write_private_json
from arga_twins_benchmark.reporting import cross_functional_semantic_report as semantic_report


def _read_object(path: Path) -> dict[str, Any]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return cast(dict[str, Any], payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_dir", type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--suite", type=Path, default=Path("benchmark/cross_functional_40/suite.json"))
    parser.add_argument("--tasks", type=Path, default=Path("benchmark/cross_functional_40/TASKS.md"))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    suite = _read_object(args.suite)
    task = next(
        (
            item
            for item in suite.get("tasks", [])
            if isinstance(item, dict) and item.get("id") == args.task_id
        ),
        None,
    )
    if task is None:
        raise ValueError(f"unknown task ID {args.task_id}")
    graders = semantic_report.build_domain_grader_registry(suite_path=args.suite, tasks_path=args.tasks)
    prefix = args.task_id.split("-", 1)[0]
    domain_grade, assertions, outcome = semantic_report._grade_completed_attempt(  # noqa: SLF001
        grader=graders.get(prefix),
        task_dir=args.task_dir,
        task=task,
    )
    if outcome == "evidence_gap":
        raise ValueError("the attempt has an executable-verifier evidence gap")
    assertions = semantic_report._enrich_structured_fact_assertions(assertions, task=task)  # noqa: SLF001
    assertions = semantic_report._enrich_unsafe_assertions(  # noqa: SLF001
        assertions,
        task_dir=args.task_dir,
        task=task,
    )
    assertions = semantic_report._enrich_decisive_assertion_evidence(  # noqa: SLF001
        assertions,
        task_dir=args.task_dir,
    )
    attempt = _read_object(args.task_dir / "attempt.json")
    result = {
        "taskId": args.task_id,
        "runId": attempt.get("run_id"),
        "profileId": attempt.get("profile_id"),
        "semanticOutcome": outcome,
        "exactReason": semantic_report._reason(outcome, assertions, None, task=task),  # noqa: SLF001
        "assertions": assertions,
        "domainGrade": domain_grade,
    }
    write_private_json(args.output, result)
    print(
        json.dumps(
            {
                "taskId": args.task_id,
                "semanticOutcome": outcome,
                "assertionCount": len(assertions),
                "output": str(args.output),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
