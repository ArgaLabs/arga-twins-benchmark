#!/usr/bin/env python3
"""Combine three scoring-ready ArgaBench semantic reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from arga_twins_benchmark.reporting.argabench_repeated_report import (
    build_argabench_repeated_report,
    write_argabench_repeated_report,
)


def _repeat_source(value: str) -> tuple[int, Path]:
    raw_repeat, separator, raw_path = value.partition("=")
    if not separator or not raw_repeat.isdigit() or not raw_path:
        raise argparse.ArgumentTypeError("repeat sources must use REPEAT=/path/to/semantic-report.json")
    return int(raw_repeat), Path(raw_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repeat",
        action="append",
        required=True,
        type=_repeat_source,
        help="Repeat semantic report as REPEAT=/path/to/semantic-report.json; provide 1, 2, and 3.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--published-at")
    parser.add_argument("--bootstrap-seed", type=int, default=20260817)
    parser.add_argument("--bootstrap-resamples", type=int, default=10_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = dict(cast(list[tuple[int, Path]], args.repeat))
    if len(paths) != len(args.repeat):
        raise ValueError("repeat numbers must be unique")
    reports: dict[int, dict[str, Any]] = {
        repeat: json.loads(path.resolve().read_text()) for repeat, path in paths.items()
    }
    report = build_argabench_repeated_report(
        reports,
        bootstrap_seed=args.bootstrap_seed,
        bootstrap_resamples=args.bootstrap_resamples,
    )
    outputs = write_argabench_repeated_report(
        report,
        args.output,
        repeat_semantic_reports=paths,
        published_at=args.published_at,
    )
    print(json.dumps({**outputs, "semantic": report["semantic"], "usage": report["usage"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
