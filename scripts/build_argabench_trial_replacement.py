#!/usr/bin/env python3
"""Bind one newly graded attempt to an existing public trial identity."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from arga_twins_benchmark.lifecycle import write_private_json


def _read_object(path: Path) -> dict[str, Any]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return cast(dict[str, Any], payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--replaces-run-id", required=True)
    parser.add_argument("--grade", type=Path, required=True)
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    grade = _read_object(args.grade)
    attempt = _read_object(args.attempt)
    if grade.get("runId") != attempt.get("run_id") or grade.get("taskId") != attempt.get("task_id"):
        raise ValueError("grade and attempt identities do not match")
    usage = attempt.get("usage")
    cost = attempt.get("cost")
    if not isinstance(usage, dict) or not isinstance(cost, dict):
        raise ValueError("attempt is missing usage or cost metrics")
    result = {
        "protocol": "argabench-trial-replacement/1",
        "trialId": args.trial_id,
        "taskId": grade["taskId"],
        "runId": grade["runId"],
        "replacesRunSha256": hashlib.sha256(args.replaces_run_id.encode()).hexdigest(),
        "semanticOutcome": grade["semanticOutcome"],
        "exactReason": grade["exactReason"],
        "assertions": grade["assertions"],
        "stopReason": attempt.get("stop_reason"),
        "terminalReason": None,
        "metrics": {
            "toolCalls": attempt["tool_calls"],
            "providerToolCalls": attempt["provider_tool_calls"],
            "officialDocsToolCalls": attempt["official_docs_tool_calls"],
            "inputTokens": usage["input_tokens"],
            "outputTokens": usage["output_tokens"],
            "estimatedCostUsd": cost["estimate"],
        },
    }
    write_private_json(args.output, result)
    print(
        json.dumps(
            {
                "trialId": args.trial_id,
                "semanticOutcome": grade["semanticOutcome"],
                "output": str(args.output),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
