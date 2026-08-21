"""Audit trace, twin-state, and verdict evidence for Cross-Functional 40 repeats.

This is deliberately an audit of the saved semantic reports and their immutable
source artifacts.  It never calls a provider or changes a Twin run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REQUIRED_ARTIFACTS = (
    "attempt",
    "baseline_state",
    "cleanup",
    "control",
    "final_state",
    "invocation",
    "official_docs_trace",
    "prompt",
    "provider_trace",
    "raw_state_diff",
    "tool_steps",
)

GENERIC_DECISIVE_DETAILS = {
    "the required business outcome is incomplete",
    "the saved activity does not show this required update being completed",
    (
        "the agent's final report did not include all of the facts needed to prove that the required business outcome "
        "was completed"
    ),
    "successful mutation fell outside the task's allowed business scope",
}

EXACT_DETAIL_SIGNALS = re.compile(
    r"(?:\bStep\s+\d+\b|/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+|“[^”]+”|"
    r"\b(?:IT|DEV|CRM|MKT|ECOM|ENG|GTM|REL|SEC|MON|DB|OFF|AUTH|API)-\d+\b|"
    r"\b(?:requires?|expected|observed|missing|omitted|contains?|retains?)\b.*\b\d+\b|"
    r"\b(?:No successful|No accepted|No Salesforce|No canonical|No active|No saved|no relevant)\b|"
    r"\b(?:does not|do not)\b|"
    r"\b(?:does|do) not establish\s*:|\bduplicate business identities\s*:|\bincluded external attendee|"
    r"\b(?:matched|missing or fact-incomplete|facts? checked|matching resource changes)\b|"
    r"\b(?:provider returned|output-token ceiling|No saved Salesforce|No new fact-specific Slack)\b|"
    r"\bChanged the protected distractor\b)",
    re.IGNORECASE,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repeat",
        action="append",
        required=True,
        metavar="LABEL=SEMANTIC_REPORT",
        help="Repeat label and semantic-report.json path; may be supplied more than once.",
    )
    parser.add_argument("--output", type=Path, required=True, help="New JSON audit ledger path.")
    return parser


def _parse_repeat(value: str) -> tuple[str, Path]:
    label, separator, path = value.partition("=")
    if not separator or not label.strip() or not path.strip():
        raise ValueError("repeat values must use LABEL=/path/to/semantic-report.json")
    return label.strip(), Path(path).expanduser().resolve()


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def _pointer_resolves(payload: object, pointer: str) -> bool:
    if pointer in {"", "/"}:
        return True
    current = payload
    for encoded in pointer.removeprefix("/").split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                return False
            current = current[token]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            try:
                index = int(token)
            except ValueError:
                return False
            if index < 0 or index >= len(current):
                return False
            current = current[index]
        else:
            return False
    return True


def _suite_pointer_resolves(task: Mapping[str, Any], pointer: str) -> bool:
    task_id = task.get("id")
    normalized = pointer
    task_prefix = f"/tasks/{task_id}" if isinstance(task_id, str) else ""
    if task_prefix and normalized.startswith(task_prefix):
        normalized = normalized[len(task_prefix) :] or "/"
    if normalized.startswith("/verification/required_outcomes/"):
        assertion_id = normalized.rsplit("/", 1)[-1]
        required = task.get("verification", {}).get("required_outcomes", [])
        return any(
            isinstance(item, Mapping)
            and item.get("id") in {assertion_id, assertion_id.replace("originating_channel", "originating_slack")}
            for item in required
        )
    return _pointer_resolves(task, normalized)


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _event_trace_matches(invocation_event: Mapping[str, Any], trace_event: Mapping[str, Any]) -> bool:
    arguments = invocation_event.get("arguments")
    output = invocation_event.get("output")
    if not isinstance(arguments, Mapping) or not isinstance(output, Mapping):
        return False
    mediated_trace = output.get("trace")
    if isinstance(mediated_trace, Mapping):
        return all(
            mediated_trace.get(field) == trace_event.get(field)
            for field in (
                "action_fingerprint",
                "attempt_fingerprint",
                "method",
                "path",
                "request_fingerprint",
                "requested_provider",
                "sequence",
                "status_code",
            )
        )
    return all(
        (
            arguments.get("provider") == trace_event.get("requested_provider"),
            arguments.get("method") == trace_event.get("method"),
            arguments.get("path") == trace_event.get("path"),
            output.get("status_code") == trace_event.get("status_code"),
        )
    )


def _audit_trial(
    *, label: str, report: Mapping[str, Any], attempt: Mapping[str, Any], suite: Mapping[str, Any]
) -> dict[str, Any]:
    source_root = Path(str(report["source_matrix_dir"]))
    artifact_paths = attempt.get("artifacts")
    if not isinstance(artifact_paths, Mapping):
        artifact_paths = {}
    missing_artifacts: list[str] = []
    artifacts: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_ARTIFACTS:
        relative = artifact_paths.get(name)
        if not isinstance(relative, str):
            missing_artifacts.append(name)
            continue
        path = source_root / relative
        if not path.is_file():
            missing_artifacts.append(name)
            continue
        try:
            artifacts[path.name] = _load_object(path)
        except (OSError, ValueError, json.JSONDecodeError):
            missing_artifacts.append(name)

    invocation = artifacts.get("invocation.json", {})
    invocation_events = invocation.get("events")
    events = invocation_events if isinstance(invocation_events, list) else []
    provider_calls = [
        event
        for event in events
        if isinstance(event, Mapping) and event.get("type") == "tool_call" and event.get("name") == "provider_api"
    ]
    docs_calls = [
        event
        for event in events
        if isinstance(event, Mapping) and event.get("type") == "tool_call" and event.get("name") == "provider_docs"
    ]
    local_rejections = [
        {
            "event_index": index,
            "tool_name": event.get("name"),
            "error_type": (event.get("output") or {}).get("error", {}).get("type")
            if isinstance(event.get("output"), Mapping)
            else None,
        }
        for index, event in enumerate(events)
        if isinstance(event, Mapping)
        and event.get("type") == "tool_call"
        and event.get("name") not in {"provider_api", "provider_docs"}
    ]
    provider_trace = artifacts.get("provider-trace.json", {}).get("events")
    provider_trace_events = provider_trace if isinstance(provider_trace, list) else []
    docs_trace = artifacts.get("official-docs-trace.json", {}).get("events")
    docs_trace_events = docs_trace if isinstance(docs_trace, list) else []
    tool_steps = artifacts.get("tool-steps.json", {}).get("steps")
    saved_steps = tool_steps if isinstance(tool_steps, list) else []

    provider_pairs_complete = len(provider_calls) == len(provider_trace_events) and all(
        _event_trace_matches(call, trace) for call, trace in zip(provider_calls, provider_trace_events, strict=True)
    )
    docs_pairs_complete = len(docs_calls) == len(docs_trace_events)
    steps_complete = len(saved_steps) == len(provider_trace_events) + len(docs_trace_events)

    baseline_queries = artifacts.get("baseline-state.json", {}).get("queries")
    final_queries = artifacts.get("final-state.json", {}).get("queries")
    before_after_complete = (
        isinstance(baseline_queries, Mapping)
        and bool(baseline_queries)
        and isinstance(final_queries, Mapping)
        and bool(final_queries)
        and set(baseline_queries) == set(final_queries)
    )

    evidence_issues: list[str] = []
    decisive_assertions: list[dict[str, Any]] = []
    assertions = attempt.get("assertions")
    for assertion in assertions if isinstance(assertions, list) else []:
        if not isinstance(assertion, Mapping) or assertion.get("status") not in {"fail", "unsafe"}:
            continue
        detail = assertion.get("detail")
        detail_text = detail.strip() if isinstance(detail, str) else ""
        pointers: list[dict[str, Any]] = []
        for evidence in assertion.get("evidence", []):
            if not isinstance(evidence, Mapping):
                continue
            artifact_name = evidence.get("artifact")
            pointer = evidence.get("pointer")
            resolved = False
            if artifact_name == "suite.json":
                resolved = _suite_pointer_resolves(suite, str(pointer or ""))
            elif isinstance(artifact_name, str) and artifact_name in artifacts:
                resolved = _pointer_resolves(artifacts[artifact_name], str(pointer or ""))
            pointers.append({"artifact": artifact_name, "pointer": pointer, "resolved": resolved})
            if not resolved:
                evidence_issues.append(f"{assertion.get('id')}: unresolved {artifact_name}{pointer}")
        if not detail_text:
            evidence_issues.append(f"{assertion.get('id')}: missing detail")
        elif detail_text.casefold() in GENERIC_DECISIVE_DETAILS:
            evidence_issues.append(f"{assertion.get('id')}: generic detail")
        elif len(detail_text) < 32 or EXACT_DETAIL_SIGNALS.search(detail_text) is None:
            evidence_issues.append(f"{assertion.get('id')}: detail lacks an exact action, target, or missing fact")
        if not pointers:
            evidence_issues.append(f"{assertion.get('id')}: no evidence pointer")
        decisive_assertions.append(
            {
                "id": assertion.get("id"),
                "status": assertion.get("status"),
                "detail": detail_text,
                "evidence": pointers,
            }
        )

    final_text = invocation.get("final_text")
    final_text_present = isinstance(final_text, str) and bool(final_text.strip())
    missing_final_reason = None
    if not final_text_present:
        missing_final_reason = {
            "model_status": invocation.get("status") or attempt.get("model_status"),
            "stop_reason": invocation.get("stop_reason") or attempt.get("stop_reason"),
            "provider_calls": len(provider_calls),
            "official_docs_calls": len(docs_calls),
            "explanation": (
                "The provider emitted no final text after the saved trajectory; this is not a missing trace."
            ),
        }

    problems: list[str] = []
    if missing_artifacts:
        problems.append("missing_or_invalid_artifacts")
    if not events:
        problems.append("missing_agent_trace")
    if not provider_pairs_complete:
        problems.append("provider_trace_mismatch")
    if not docs_pairs_complete:
        problems.append("official_docs_trace_mismatch")
    if not steps_complete:
        problems.append("tool_step_trace_mismatch")
    if not before_after_complete:
        problems.append("missing_before_after_query_state")
    if evidence_issues:
        problems.append("decisive_assertion_evidence_issue")
    outcome = attempt.get("semantic_outcome")
    decisive_statuses = {
        assertion.get("status")
        for assertion in assertions if isinstance(assertions, list) and isinstance(assertion, Mapping)
    }
    if outcome == "unsafe" and "unsafe" not in decisive_statuses:
        problems.append("unsafe_outcome_without_unsafe_assertion")
    elif outcome == "fail" and "fail" not in decisive_statuses:
        problems.append("fail_outcome_without_fail_assertion")
    elif outcome == "pass" and decisive_statuses & {"fail", "unsafe", "evidence_gap"}:
        problems.append("pass_outcome_with_decisive_failure")
    if attempt.get("prompt") != suite.get("prompt"):
        problems.append("prompt_contract_mismatch")

    return {
        "repeat": label,
        "trial_id": attempt.get("trial_id"),
        "profile_id": attempt.get("profile_id"),
        "task_id": attempt.get("task_id"),
        "semantic_outcome": attempt.get("semantic_outcome"),
        "verdict_status_consistent": not any(
            problem.endswith("assertion") or problem == "pass_outcome_with_decisive_failure" for problem in problems
        ),
        "agent_trace": {
            "present": bool(events),
            "event_count": len(events),
            "provider_call_count": len(provider_calls),
            "official_docs_call_count": len(docs_calls),
            "saved_tool_step_count": len(saved_steps),
            "provider_pairs_complete": provider_pairs_complete,
            "official_docs_pairs_complete": docs_pairs_complete,
            "tool_steps_complete": steps_complete,
            "local_rejected_tool_calls": local_rejections,
        },
        "twin_state": {
            "before_after_complete": before_after_complete,
            "query_count": len(baseline_queries) if isinstance(baseline_queries, Mapping) else 0,
            "baseline_fingerprint": _fingerprint(baseline_queries) if isinstance(baseline_queries, Mapping) else None,
            "final_fingerprint": _fingerprint(final_queries) if isinstance(final_queries, Mapping) else None,
        },
        "final_response": {"present": final_text_present, "missing_reason": missing_final_reason},
        "decisive_assertions": decisive_assertions,
        "evidence_issues": evidence_issues,
        "missing_artifacts": missing_artifacts,
        "problems": problems,
        "audit_passed": not problems,
    }


def main() -> int:
    args = _parser().parse_args()
    repeats = [_parse_repeat(value) for value in args.repeat]
    if args.output.exists():
        raise ValueError(f"output already exists: {args.output}")

    suite_path = Path(__file__).resolve().parents[1] / "benchmark" / "cross_functional_40" / "suite.json"
    suite_payload = _load_object(suite_path)
    suite_by_task = {
        str(task["id"]): task
        for task in suite_payload.get("tasks", [])
        if isinstance(task, Mapping) and isinstance(task.get("id"), str)
    }

    trials: list[dict[str, Any]] = []
    seen_trials: set[tuple[str, str]] = set()
    for label, report_path in repeats:
        report = _load_object(report_path)
        for attempt in report.get("attempts", []):
            if not isinstance(attempt, Mapping):
                continue
            task_id = attempt.get("task_id")
            suite_task = suite_by_task.get(str(task_id), {})
            trial_id = attempt.get("trial_id")
            identity = (label, str(trial_id))
            if identity in seen_trials:
                raise ValueError(f"duplicate trial identity: {label}/{trial_id}")
            seen_trials.add(identity)
            trials.append(_audit_trial(label=label, report=report, attempt=attempt, suite=suite_task))

    missing_final = [trial for trial in trials if not trial["final_response"]["present"]]
    local_rejections = [
        {
            "repeat": trial["repeat"],
            "trial_id": trial["trial_id"],
            "profile_id": trial["profile_id"],
            "task_id": trial["task_id"],
            "events": trial["agent_trace"]["local_rejected_tool_calls"],
        }
        for trial in trials
        if trial["agent_trace"]["local_rejected_tool_calls"]
    ]
    problem_trials = [trial for trial in trials if not trial["audit_passed"]]
    payload = {
        "protocol": "arga-bench-cross-functional-trial-evidence-audit/1",
        "scope": {
            "repeat_count": len(repeats),
            "trial_count": len(trials),
            "semantic_outcomes": {
                outcome: sum(trial["semantic_outcome"] == outcome for trial in trials)
                for outcome in ("pass", "fail", "unsafe")
            },
        },
        "summary": {
            "trials_with_agent_trace": sum(trial["agent_trace"]["present"] for trial in trials),
            "trials_with_complete_provider_trace": sum(
                trial["agent_trace"]["provider_pairs_complete"] for trial in trials
            ),
            "trials_with_complete_official_docs_trace": sum(
                trial["agent_trace"]["official_docs_pairs_complete"] for trial in trials
            ),
            "trials_with_complete_before_after_state": sum(
                trial["twin_state"]["before_after_complete"] for trial in trials
            ),
            "trials_without_final_response": len(missing_final),
            "trials_with_local_rejected_tool_calls": len(local_rejections),
            "trials_passing_audit": len(trials) - len(problem_trials),
            "trials_failing_audit": len(problem_trials),
        },
        "missing_final_responses": [
            {
                "repeat": trial["repeat"],
                "trial_id": trial["trial_id"],
                "profile_id": trial["profile_id"],
                "task_id": trial["task_id"],
                **trial["final_response"]["missing_reason"],
            }
            for trial in missing_final
        ],
        "local_rejected_tool_calls": local_rejections,
        "problem_trials": problem_trials,
        "trials": trials,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0 if not problem_trials else 1


if __name__ == "__main__":
    raise SystemExit(main())
