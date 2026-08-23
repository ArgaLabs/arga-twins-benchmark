#!/usr/bin/env python3
"""Select task-scoped evidence from an existing semantic matrix report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from arga_twins_benchmark.reporting.cross_functional_semantic_report import (
    select_cross_functional_semantic_report,
    write_cross_functional_semantic_report,
)

ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "benchmark" / "cross_functional_40" / "suite.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("semantic_report", type=Path, help="Existing offline semantic-report.json.")
    parser.add_argument("output_dir", type=Path, help="New task-scoped report directory.")
    parser.add_argument("--task", action="append", required=True, dest="task_ids")
    parser.add_argument("--published-at", help="Publication manifest date in YYYY-MM-DD form.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload: object = json.loads(args.semantic_report.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("semantic report must contain a JSON object")
    report = select_cross_functional_semantic_report(cast(dict[str, Any], payload), args.task_ids)
    source_matrix = report.get("source_matrix_dir")
    if not isinstance(source_matrix, str):
        raise ValueError("semantic report is missing its source matrix directory")
    outputs = write_cross_functional_semantic_report(
        report,
        args.output_dir,
        source_matrix_dir=Path(source_matrix),
        suite_path=SUITE_PATH,
        published_at=args.published_at,
    )
    print(
        json.dumps(
            {
                **outputs,
                "scheduled": report["totals"]["scheduled"],
                "validity": report["totals"]["validity"],
                "semantic": report["totals"]["semantic"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
