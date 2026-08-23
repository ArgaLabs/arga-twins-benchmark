#!/usr/bin/env python3
"""Validate and report one fresh Cross-Functional 40 rerun."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "benchmark" / "cross_functional_40" / "suite.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    suite = load_json(SUITE_PATH)
    tasks = suite["tasks"]
    grading = load_json(run_dir / "grading.json")
    verdicts = grading["verdicts"]
    scenario_ids = load_json(run_dir / "staging-scenarios.json")["scenario_ids"]
    task_ids = [task["id"] for task in tasks]
    if set(task_ids) != set(verdicts) or set(task_ids) != set(scenario_ids):
        raise RuntimeError("suite, grading, and Scenario mappings must cover the same 40 task IDs")

    records: list[dict[str, Any]] = []
    all_steps: dict[str, list[dict[str, Any]]] = {}
    outputs: dict[str, str] = {}
    run_ids: list[str] = []
    for task in tasks:
        task_id = task["id"]
        task_dir = run_dir / "tasks" / task_id
        attempt = load_json(task_dir / "attempt.json")
        checks = {
            "candidate_complete": attempt.get("attempt_status") == "candidate_complete",
            "model_completed": attempt.get("model_status") == "completed",
            "exact_response_model": attempt.get("response_model") == "claude-fable-5",
            "cleanup_succeeded": attempt.get("cleanup_succeeded") is True,
            "prompt_exact": attempt.get("prompt") == task["prompt"],
            "scenario_exact": attempt.get("scenario_id") == scenario_ids[task_id],
            "trace_present": (task_dir / "provider-trace.json").is_file(),
            "state_diff_present": (task_dir / "raw-state-diff.json").is_file(),
        }
        if not all(checks.values()):
            raise RuntimeError(f"{task_id}: invalid scoring artifact: {checks}")
        run_ids.append(attempt["run_id"])
        all_steps[task_id] = load_json(task_dir / "tool-steps.json")["steps"]
        outputs[task_id] = attempt["final_text"]
        verdict = verdicts[task_id]
        records.append(
            {
                "task_id": task_id,
                "title": task["title"],
                "domain": task["domain"],
                "prompt": task["prompt"],
                "passed": verdict["passed"],
                "result": "pass" if verdict["passed"] else "fail",
                "reason": verdict["reason"],
                "run_id": attempt["run_id"],
                "scenario_id": attempt["scenario_id"],
                "stop_reason": attempt["stop_reason"],
                "tool_calls": attempt["tool_calls"],
                "provider_tool_calls": attempt["provider_tool_calls"],
                "official_docs_tool_calls": attempt["official_docs_tool_calls"],
                "input_tokens": attempt["usage"].get("input_tokens", 0),
                "output_tokens": attempt["output_tokens"],
                "estimated_cost_usd": attempt["cost"]["estimate"],
                "cleanup_succeeded": True,
                "artifacts": {
                    "attempt": str((task_dir / "attempt.json").relative_to(run_dir)),
                    "tool_steps": str((task_dir / "tool-steps.json").relative_to(run_dir)),
                    "provider_trace": str((task_dir / "provider-trace.json").relative_to(run_dir)),
                    "state_diff": str((task_dir / "raw-state-diff.json").relative_to(run_dir)),
                },
            }
        )
    if len(records) != 40 or len(set(run_ids)) != 40:
        raise RuntimeError("scoring set must contain 40 records and 40 unique twin run IDs")

    passes = sum(record["passed"] for record in records)
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_domain[record["domain"]].append(record)
    domains = {
        domain: {
            "attempts": len(items),
            "passes": sum(item["passed"] for item in items),
            "fails": sum(not item["passed"] for item in items),
            "pass_rate": sum(item["passed"] for item in items) / len(items),
        }
        for domain, items in sorted(by_domain.items())
    }
    result = {
        "protocol": "arga-bench-cross-functional-results/2",
        "suite_id": suite["suite_id"],
        "model": "claude-fable-5",
        "effort": "high",
        "environment": "staging",
        "concurrency": 10,
        "attempts_per_scenario": 1,
        "attempts": 40,
        "passes": passes,
        "fails": 40 - passes,
        "pass_rate": passes / 40,
        "estimated_cost_usd": round(sum(item["estimated_cost_usd"] for item in records), 8),
        "input_tokens": sum(item["input_tokens"] for item in records),
        "output_tokens": sum(item["output_tokens"] for item in records),
        "tool_calls": sum(item["tool_calls"] for item in records),
        "provider_tool_calls": sum(item["provider_tool_calls"] for item in records),
        "official_docs_tool_calls": sum(item["official_docs_tool_calls"] for item in records),
        "cleanups_succeeded": sum(item["cleanup_succeeded"] for item in records),
        "stop_reasons": dict(sorted(Counter(item["stop_reason"] for item in records).items())),
        "domains": domains,
        "grading": {
            "method": grading["method"],
            "all_critical_required_outcomes_must_pass": True,
            "any_critical_forbidden_outcome_fails": True,
            "trajectory_order_graded": grading["trajectory_order_graded"],
            "final_response_required": grading["final_response_required"],
        },
        "tasks": records,
    }
    (run_dir / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (run_dir / "TOOL_STEPS.json").write_text(
        json.dumps(
            {
                "protocol": "arga-bench-cross-functional-tool-steps/2",
                "task_count": 40,
                "tool_call_count": result["tool_calls"],
                "tasks": all_steps,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    final_lines = ["# Final candidate outputs", ""]
    prompt_lines = ["# Prompts used", ""]
    for task in tasks:
        final_lines.extend([f"## {task['id']} — {task['title']}", "", outputs[task["id"]], ""])
        prompt_lines.extend([f"## {task['id']} — {task['title']}", "", task["prompt"], ""])
    (run_dir / "FINAL_OUTPUTS.md").write_text("\n".join(final_lines))
    (run_dir / "PROMPTS_USED.md").write_text("\n".join(prompt_lines))

    lines = [
        "# Cross-Functional 40 — Fable 5 High fairness-revision rerun",
        "",
        f"- Result: **{passes}/40 pass ({passes / 40:.1%})**",
        f"- Estimated cost: **${result['estimated_cost_usd']:.4f}**",
        f"- Input tokens: **{result['input_tokens']:,}**",
        f"- Output tokens: **{result['output_tokens']:,}**",
        f"- Tool calls: **{result['tool_calls']:,}**",
        f"- Clean teardowns: **{result['cleanups_succeeded']}/40**",
        "- Attempts: **one fresh Fable 5 High invocation per revised Scenario**",
        "- Concurrency: **10**",
        "",
        "Cost is estimated from Anthropic-reported usage at the recorded Fable rates; it is not invoice data.",
        "",
        "## Domain results",
        "",
        "| Domain | Pass | Fail | Pass rate |",
        "|---|---:|---:|---:|",
    ]
    for domain, aggregate in domains.items():
        lines.append(f"| {domain} | {aggregate['passes']} | {aggregate['fails']} | {aggregate['pass_rate']:.1%} |")
    lines.extend(
        [
            "",
            "## Task results",
            "",
            "| Task | Result | Tools | Output tokens | Est. cost | Decisive evidence |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for record in records:
        reason = record["reason"].replace("|", "\\|")
        lines.append(
            f"| {record['task_id']} | {record['result'].upper()} | {record['tool_calls']} | "
            f"{record['output_tokens']:,} | ${record['estimated_cost_usd']:.4f} | {reason} |"
        )
    lines.extend(
        [
            "",
            "## Grading contract",
            "",
            "Every critical required business outcome had to pass; any critical forbidden side effect caused failure. "
            "Provider order, retry count, and final-response formatting were not graded. Evidence was accepted from "
            "trusted provider state, provider traces, authorized internal updates, or the candidate response.",
        ]
    )
    (run_dir / "RESULTS.md").write_text("\n".join(lines) + "\n")
    summary_keys = (
        "passes",
        "fails",
        "pass_rate",
        "estimated_cost_usd",
        "input_tokens",
        "output_tokens",
        "tool_calls",
        "cleanups_succeeded",
        "domains",
    )
    print(json.dumps({key: result[key] for key in summary_keys}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
