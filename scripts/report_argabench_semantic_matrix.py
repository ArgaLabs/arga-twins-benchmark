#!/usr/bin/env python3
"""Build the offline semantic report and gated website exports for ArgaBench."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from arga_twins_benchmark.reporting.argabench_semantic_report import (
    build_argabench_semantic_report,
    write_argabench_semantic_report,
)

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = ROOT / "benchmark" / "argabench_40"
SUITE_PATH = BENCHMARK_ROOT / "suite.json"
TASKS_PATH = BENCHMARK_ROOT / "TASKS.md"
MODEL_MATRIX_PATH = BENCHMARK_ROOT / "model_matrix.json"
HISTORICAL_CALIBRATION_PATH = BENCHMARK_ROOT / "historical_fable_5_high_fairness_calibration.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix_dir", type=Path, help="Preserved model matrix directory (read-only).")
    parser.add_argument("output_dir", type=Path, help="New report directory outside matrix_dir.")
    parser.add_argument("--published-at", help="Publication manifest date in YYYY-MM-DD form.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    matrix_dir = args.matrix_dir.resolve()
    report = build_argabench_semantic_report(
        matrix_dir,
        suite_path=SUITE_PATH,
        tasks_path=TASKS_PATH,
        model_matrix_path=MODEL_MATRIX_PATH,
        historical_calibration_path=HISTORICAL_CALIBRATION_PATH,
    )
    outputs = write_argabench_semantic_report(
        report,
        args.output_dir,
        source_matrix_dir=matrix_dir,
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
