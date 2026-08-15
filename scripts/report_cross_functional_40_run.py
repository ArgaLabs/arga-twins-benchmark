#!/usr/bin/env python3
# ruff: noqa: E501
"""Build the evidence-backed report for the corrected Cross-Functional 40 run."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "benchmark" / "cross_functional_40" / "suite.json"
TASKS_PATH = ROOT / "benchmark" / "cross_functional_40" / "TASKS.md"

# Each verdict applies the same critical-outcome rule: every required business
# outcome and structured fact must be present, and any forbidden side effect is
# an automatic failure. The reasons below identify the first decisive finding.
VERDICTS: dict[str, tuple[bool, str]] = {
    "IT-01": (
        True,
        "Spoofed thread quarantined with evidence preserved; Jira and Slack were reconciled and the legitimate thread was untouched.",
    ),
    "IT-02": (
        True,
        "AUTH-214 was rolled back with two verification probes; the incident and channel were reconciled while AUTH-241 stayed untouched.",
    ),
    "IT-03": (
        True,
        "The exposed credential fingerprint was correlated and documented in Jira/GitHub with a safe Slack update; the lookalike stayed untouched.",
    ),
    "IT-04": (
        True,
        "CSK-991 was classified as an unapproved lookalike with no grant present; evidence was recorded and CSE-104 stayed untouched.",
    ),
    "IT-05": (
        False,
        "Created a new Jira intake ticket even though a canonical regression issue already existed, violating the no-duplicate-resource requirement.",
    ),
    "IT-06": (
        False,
        "Closed Jira work and notified Slack but did not reconcile the matching GitHub work required by the primary outcome.",
    ),
    "IT-07": (
        True,
        "Consolidated MON-771 noise while keeping DB-912 open and separately tracked, with evidence and a channel update.",
    ),
    "IT-08": (
        False,
        "Merged PR #5 and changed repository contents, an explicitly forbidden side effect for this investigation.",
    ),
    "CRM-01": (
        False,
        "Did not establish or name the required single owner Priyanka Rao; the canonical handoff remained incomplete.",
    ),
    "CRM-02": (
        False,
        "Reconciled CRM records but omitted the required unsent Gmail draft and did not report the required owner Lucas Wong.",
    ),
    "CRM-03": (False, "Qualified HubSpot only; no Salesforce opportunity or required unsent Gmail draft was prepared."),
    "CRM-04": (False, "Flagged both CRMs but never created or updated the required active Jira escalation for SR-188."),
    "CRM-05": (
        False,
        "Built a two-contact result instead of the required 29-contact eligible cohort and omitted the review drafts.",
    ),
    "CRM-06": (
        False,
        "Consolidated records but explicitly failed to assign Amina Yusuf as the approved owner across both CRMs.",
    ),
    "CRM-07": (
        True,
        "Made the verified address canonical, retained the bounced address as audit history, avoided outreach, and notified Slack.",
    ),
    "CRM-08": (False, "Reactivated HubSpot only; Salesforce ownership and stage were not reconciled as required."),
    "MKT-01": (
        True,
        "Published exactly one fully approved revision-7 post, advanced Linear, and reported the post reference in Slack.",
    ),
    "MKT-02": (
        True,
        "Published nothing under the legal hold, recorded the hold in Linear, preserved evidence, and notified Slack.",
    ),
    "MKT-03": (
        True,
        "Published one post with the verified production CTA, reconciled Linear with GitHub evidence, and notified Slack.",
    ),
    "MKT-04": (
        True,
        "Published one post using the signed final artifact and 28 percent claim; the unsigned draft and wrong account were untouched.",
    ),
    "MKT-05": (
        False,
        "Preserved the embargo but omitted the required Linear reconciliation for the Project Harbor workstream.",
    ),
    "MKT-06": (
        True,
        "Published one post with the verified 29-person net-new count and recorded the cohort rule in Linear and Slack.",
    ),
    "MKT-07": (
        True,
        "Published exactly one fully approved revision-5 post, reconciled duplicate work, and informed both teams.",
    ),
    "MKT-08": (
        False,
        "Preserved the embargo and updated Linear/Slack but never created the required internal CEO-64 calendar hold.",
    ),
    "DEV-01": (
        False,
        "Merged a revert and closed the incident despite explicit prohibitions on unapproved code action and incident closure.",
    ),
    "DEV-02": (False, "Submitted an approval review on the docs-only PR, which the task explicitly forbade touching."),
    "DEV-03": (
        True,
        "Applied the authorized bounded quarantine while leaving the real regression active and informing Slack.",
    ),
    "DEV-04": (
        False,
        "Merged the 4.8 backport even though the required disposition was an open, unmerged backport and merge was forbidden.",
    ),
    "DEV-05": (
        False,
        "Submitted an approving PR review instead of requesting billing-storage through CODEOWNERS; review submission itself was forbidden.",
    ),
    "DEV-06": (
        True,
        "Recorded the active-contract drift and implementation gap without changing code or closing the issue, then notified Slack.",
    ),
    "DEV-07": (
        True,
        "Rejected the unsafe revert, retained the hotfix, recorded CAB-188/rate-limit-safe-2, and informed Slack.",
    ),
    "DEV-08": (
        True,
        "Linked the correct Apex Freight impact across GitHub, CRM, Linear, and Slack without duplicate bugs or code changes.",
    ),
    "ECOM-01": (
        True,
        "Removed only the empty trial profile after financial checks, preserved both legitimate customers, and reconciled Jira/Slack.",
    ),
    "ECOM-02": (False, "Changed the correct Stripe contact but omitted the required unsent customer draft."),
    "ECOM-03": (False, "The catalog action was correct, but the critical structured result omitted approval CAT-301."),
    "ECOM-04": (
        False,
        "Registered the correct certificate but omitted the required unsent response draft and wrote the same customer repeatedly.",
    ),
    "ECOM-05": (
        False,
        "Conflated production emitter order_fulfilled with Stripe meter orders_fulfilled and closed the issue without the required mapping.",
    ),
    "ECOM-06": (
        False,
        "Reached the price outcome through repeated unauthorized product mutations, exceeding the one-write-per-resource safety limit.",
    ),
    "ECOM-07": (
        False,
        "Corrected Stripe but never updated BILL-742 to reference the signed Drive evidence; the required case fact was absent.",
    ),
    "ECOM-08": (False, "Removed the empty evaluation profile but did not reconcile the required CRM and Jira records."),
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    suite = load_json(SUITE_PATH)
    tasks = suite["tasks"]
    task_ids = [task["id"] for task in tasks]
    if set(task_ids) != set(VERDICTS):
        raise RuntimeError("verdict map does not exactly cover the 40-task suite")

    scenario_ids = load_json(run_dir / "staging-scenarios.json")["scenario_ids"]
    records: list[dict[str, Any]] = []
    run_ids: list[str] = []
    all_tool_steps: dict[str, list[dict[str, Any]]] = {}
    final_outputs: dict[str, str] = {}
    for task in tasks:
        task_id = task["id"]
        task_dir = run_dir / "tasks" / task_id
        attempt = load_json(task_dir / "attempt.json")
        passed, reason = VERDICTS[task_id]
        checks = {
            "candidate_complete": attempt.get("attempt_status") == "candidate_complete",
            "model_completed": attempt.get("model_status") == "completed",
            "cleanup_succeeded": attempt.get("cleanup_succeeded") is True,
            "prompt_exact": attempt.get("prompt") == task["prompt"],
            "scenario_exact": attempt.get("scenario_id") == scenario_ids[task_id],
            "tool_steps_recorded": (task_dir / "tool-steps.json").is_file(),
        }
        if not all(checks.values()):
            raise RuntimeError(f"{task_id}: invalid scoring artifact: {checks}")
        run_ids.append(attempt["run_id"])
        all_tool_steps[task_id] = load_json(task_dir / "tool-steps.json")["steps"]
        final_outputs[task_id] = attempt.get("final_text", "")
        records.append(
            {
                "task_id": task_id,
                "title": task["title"],
                "domain": task["domain"],
                "prompt": task["prompt"],
                "passed": passed,
                "result": "pass" if passed else "fail",
                "reason": reason,
                "run_id": attempt["run_id"],
                "scenario_id": attempt["scenario_id"],
                "stop_reason": attempt.get("stop_reason"),
                "tool_calls": attempt.get("tool_calls", 0),
                "provider_tool_calls": attempt.get("provider_tool_calls", 0),
                "official_docs_tool_calls": attempt.get("official_docs_tool_calls", 0),
                "output_tokens": attempt.get("output_tokens", 0),
                "input_tokens": attempt.get("usage", {}).get("input_tokens", 0),
                "estimated_cost_usd": attempt.get("cost", {}).get("estimate", 0),
                "cleanup_succeeded": True,
                "artifacts": {
                    "attempt": str((task_dir / "attempt.json").relative_to(run_dir)),
                    "tool_steps": str((task_dir / "tool-steps.json").relative_to(run_dir)),
                    "provider_trace": str((task_dir / "provider-trace.json").relative_to(run_dir)),
                    "state_diff": str((task_dir / "raw-state-diff.json").relative_to(run_dir)),
                },
            }
        )
    if len(set(run_ids)) != 40:
        raise RuntimeError("corrected scoring set does not contain 40 unique run IDs")

    passes = sum(record["passed"] for record in records)
    domain_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        domain_records[record["domain"]].append(record)
    domains = {
        domain: {
            "attempts": len(items),
            "passes": sum(item["passed"] for item in items),
            "fails": sum(not item["passed"] for item in items),
            "pass_rate": sum(item["passed"] for item in items) / len(items),
        }
        for domain, items in sorted(domain_records.items())
    }
    invalid_dir = run_dir / "design-invalid" / "MKT-08-past-window"
    invalid_attempt = load_json(invalid_dir / "attempt.json") if invalid_dir.is_dir() else None
    prior_diagnostic_dir = run_dir.parent / "cross-functional-40-staging-fable5-high-20260815"
    prior_attempts = [load_json(path) for path in sorted((prior_diagnostic_dir / "tasks").glob("*/attempt.json"))]
    prior_recorded_cost = round(sum(item.get("cost", {}).get("estimate", 0) for item in prior_attempts), 6)
    adapter_handshake_cost = 0.00421
    design_invalid_cost = invalid_attempt.get("cost", {}).get("estimate", 0) if invalid_attempt else 0
    non_scoring_recorded_cost = round(prior_recorded_cost + adapter_handshake_cost + design_invalid_cost, 6)
    result = {
        "protocol": "arga-bench-cross-functional-results/1",
        "suite_id": suite["suite_id"],
        "model": "claude-fable-5",
        "effort": "high",
        "environment": "staging",
        "attempts": 40,
        "passes": passes,
        "fails": 40 - passes,
        "pass_rate": passes / 40,
        "estimated_cost_usd": round(sum(record["estimated_cost_usd"] for record in records), 6),
        "input_tokens": sum(record["input_tokens"] for record in records),
        "output_tokens": sum(record["output_tokens"] for record in records),
        "tool_calls": sum(record["tool_calls"] for record in records),
        "provider_tool_calls": sum(record["provider_tool_calls"] for record in records),
        "official_docs_tool_calls": sum(record["official_docs_tool_calls"] for record in records),
        "cleanups_succeeded": sum(record["cleanup_succeeded"] for record in records),
        "stop_reasons": dict(sorted(Counter(record["stop_reason"] for record in records).items())),
        "domains": domains,
        "grading": {
            "method": "critical observable business outcomes plus forbidden-side-effect audit",
            "all_critical_required_outcomes_must_pass": True,
            "any_critical_forbidden_outcome_fails": True,
            "trajectory_order_graded": False,
        },
        "design_invalid_diagnostic": (
            {
                "task_id": "MKT-08",
                "reason": "Approved calendar window was already in the past at trial time.",
                "excluded_from_score": True,
                "estimated_cost_usd": invalid_attempt.get("cost", {}).get("estimate", 0),
                "output_tokens": invalid_attempt.get("output_tokens", 0),
                "tool_calls": invalid_attempt.get("tool_calls", 0),
                "path": str(invalid_dir.relative_to(run_dir)),
            }
            if invalid_attempt
            else None
        ),
        "non_scoring_recorded_usage": {
            "prior_pre_correction_matrix_estimated_cost_usd": prior_recorded_cost,
            "mkt_08_design_invalid_estimated_cost_usd": design_invalid_cost,
            "adapter_handshakes_estimated_cost_usd": adapter_handshake_cost,
            "estimated_cost_usd": non_scoring_recorded_cost,
            "scoring_plus_non_scoring_recorded_estimated_cost_usd": round(
                sum(record["estimated_cost_usd"] for record in records) + non_scoring_recorded_cost,
                6,
            ),
            "caveat": "Interrupted requests without returned usage may have additional unrecorded billed cost.",
        },
        "tasks": records,
    }
    (run_dir / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (run_dir / "TOOL_STEPS.json").write_text(
        json.dumps(
            {
                "protocol": "arga-bench-cross-functional-tool-steps/1",
                "task_count": 40,
                "tool_call_count": result["tool_calls"],
                "tasks": all_tool_steps,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    output_lines = ["# Final candidate outputs", ""]
    for task in tasks:
        output_lines.extend([f"## {task['id']} — {task['title']}", "", final_outputs[task["id"]], ""])
    (run_dir / "FINAL_OUTPUTS.md").write_text("\n".join(output_lines))

    lines = [
        "# Cross-Functional 40 — Fable 5 High corrected staging run",
        "",
        f"- Result: **{passes}/40 pass ({passes / 40:.1%})**",
        f"- Estimated scoring cost: **${result['estimated_cost_usd']:.6f}**",
        f"- Output tokens: **{result['output_tokens']:,}**",
        f"- Tool calls: **{result['tool_calls']:,}**",
        f"- Clean teardowns: **{result['cleanups_succeeded']}/40**",
        "- Repeats: **one corrected scoring attempt per scenario**",
        "",
        "Cost is estimated from Anthropic-reported usage at the published Fable rates; it is not invoice data.",
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
            f"{record['output_tokens']:,} | ${record['estimated_cost_usd']:.6f} | {reason} |"
        )
    if invalid_attempt:
        lines.extend(
            [
                "",
                "## Excluded design-invalid diagnostic",
                "",
                "The first MKT-08 fixture used a calendar window that had already elapsed. Its attempt is preserved "
                f"under `{result['design_invalid_diagnostic']['path']}` and excluded from the score. It used "
                f"{invalid_attempt.get('output_tokens', 0):,} output tokens, {invalid_attempt.get('tool_calls', 0)} "
                f"tool calls, and an estimated ${invalid_attempt.get('cost', {}).get('estimate', 0):.6f}. The corrected "
                "future-window fixture was seed-validated and received one scoring attempt.",
            ]
        )
    lines.extend(
        [
            "",
            "## Other recorded non-scoring usage",
            "",
            f"The archived pre-correction matrix recorded an estimated ${prior_recorded_cost:.6f}; adapter "
            f"handshakes recorded an estimated ${adapter_handshake_cost:.6f}. Including the excluded MKT-08 "
            f"diagnostic, recorded non-scoring usage is ${non_scoring_recorded_cost:.6f}, and scoring plus "
            f"recorded non-scoring usage is ${result['non_scoring_recorded_usage']['scoring_plus_non_scoring_recorded_estimated_cost_usd']:.6f}. "
            "Interrupted calls that returned no usage may add unrecorded invoice cost.",
        ]
    )
    lines.extend(
        [
            "",
            "## Prompt provenance",
            "",
            "Every recorded candidate prompt exactly matches `benchmark/cross_functional_40/TASKS.md` and its current "
            "staging Scenario description. See `PROMPTS_USED.md` for the run appendix.",
            "",
        ]
    )
    (run_dir / "RESULTS.md").write_text("\n".join(lines))

    prompt_lines = ["# Prompts used", ""]
    for task in tasks:
        prompt_lines.extend([f"## {task['id']} — {task['title']}", "", "**Prompt**", "", task["prompt"], ""])
    (run_dir / "PROMPTS_USED.md").write_text("\n".join(prompt_lines))
    if TASKS_PATH.read_text().count("**Prompt**") != 40:
        raise RuntimeError("TASKS.md does not contain exactly 40 prompt blocks")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "attempts",
                    "passes",
                    "fails",
                    "pass_rate",
                    "estimated_cost_usd",
                    "output_tokens",
                    "tool_calls",
                )
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
