from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from arga_twins_benchmark.reporting.suite_audit import SuiteAuditError, audit_suite


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit a saved Arga benchmark suite using only local artifacts.",
    )
    parser.add_argument("suite_dir", type=Path, help="Suite directory containing suite.json and trials/")
    parser.add_argument(
        "--prompt-ledger",
        type=Path,
        help="Prompt ledger to audit against (default: SUITE_DIR/prompt-ledger.json)",
    )
    parser.add_argument(
        "--minimum-tool-calls",
        type=int,
        default=6,
        help="Diagnostic provider-call floor for every completed trial (default: 6)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit compact rather than indented JSON",
    )
    parser.add_argument(
        "--fail-unless-scoring-ready",
        action="store_true",
        help="Exit 1 unless every trial is evaluable and the audit is scoring-ready",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = audit_suite(
            args.suite_dir,
            prompt_ledger=args.prompt_ledger,
            minimum_tool_calls=args.minimum_tool_calls,
        )
    except SuiteAuditError as error:
        print(f"audit error: {error}", file=sys.stderr)
        return 2

    indent = None if args.compact else 2
    print(json.dumps(report, indent=indent, sort_keys=True))
    if args.fail_unless_scoring_ready and not report["scoring_ready"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
