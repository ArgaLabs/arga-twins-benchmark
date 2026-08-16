#!/usr/bin/env python3
"""Build a fail-closed offline classification of a Cross-Functional 40 matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from arga_twins_benchmark.reporting.cross_functional_matrix import (
    classify_cross_functional_matrix,
    write_cross_functional_matrix_report,
)

ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "benchmark" / "cross_functional_40" / "suite.json"
MODEL_MATRIX_PATH = ROOT / "benchmark" / "cross_functional_40" / "model_matrix.json"
HISTORICAL_CALIBRATION_PATH = (
    ROOT
    / "benchmark"
    / "cross_functional_40"
    / "historical_fable_5_high_fairness_calibration.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix_dir", type=Path, help="Preserved matrix run directory (read-only).")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON destination. It must be outside matrix_dir; without it the report is printed.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    matrix_dir = args.matrix_dir.resolve()
    report = classify_cross_functional_matrix(
        matrix_dir,
        suite_path=SUITE_PATH,
        model_matrix_path=MODEL_MATRIX_PATH,
        historical_calibration_path=HISTORICAL_CALIBRATION_PATH,
    )
    if args.output is None:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        write_cross_functional_matrix_report(
            report,
            args.output,
            source_matrix_dir=matrix_dir,
        )
        print(
            json.dumps(
                {
                    "output": str(args.output.resolve()),
                    "scheduled_attempts": report["totals"]["scheduled_attempts"],
                    "validity": report["totals"]["validity"],
                    "score_denominator": report["totals"]["scoring"]["denominator"],
                },
                indent=2,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
