# Arga Twins Benchmark

A provider-general, stateful benchmark for evaluating whether agents can complete useful work, respect authorization boundaries, recover from operational failures, and transfer the same workflow across service providers.

This repository owns benchmark semantics, agent execution, grading, and experiment orchestration. The authenticated Arga CLI provisions and manages deterministic service twins; candidate agents run separately and call the resulting provider APIs.

## Current status

The development catalog contains 12 semantic task families with four variants each: 48 scored episodes with exact twin seeds, authorization envelopes, explicit six-or-more-step evidence graphs, and executable deterministic verification manifests. One-action API checks are separate smoke/conformance material and do not count toward the scored 48. The catalog remains a benchmark candidate rather than a public leaderboard until every episode passes live twin conformance, gold-solution, negative-control, and isolation gates.

## Principles

1. Author semantic workflows independently of provider names.
2. Store exact seeds; never use natural-language scenario generation for scored runs.
3. Evaluate final canonical state and semantic side effects rather than one prescribed tool trajectory.
4. Pair unsafe cases with nearly identical authorized cases.
5. Report capability, safety, robustness, transfer, and infrastructure validity separately.
6. Keep candidate data-plane access separate from hidden grader and Arga CLI access; models see provider roles and safe tool schemas, never twin URLs or credentials.
7. Author at least six semantically necessary provider interactions in every scored episode; use the trace as a depth and efficiency diagnostic, not a hidden gold-path success gate.
8. Default-deny unlisted semantic mutations and use the trusted candidate-call ledger to enforce external/control-plane safety and diagnose inefficient trajectories.
9. Require the correct structured decision as well as correct provider state.
10. Score only fixture behavior installed by exact Scenario seeds; prose-only fault schedules are not executable evidence.

Task success is outcome-first. Critical final-state assertions, required and forbidden semantic side effects, critical structured result facts, and external/control-plane safety are hard gates. Exact API routes, call ordering, minimum call counts, and route allowlist conformance are reported separately as trajectory diagnostics. Five action-equivalent candidate calls trigger a redundancy flag when the trusted trace can prove equivalence, but redundancy alone does not fail an otherwise correct and safe episode.

## Repository map

```text
benchmark/                 Worlds, templates, bindings, instances, and experiments
docs/                      Architecture, contracts, task catalog, security, and roadmap
schemas/                   Generated JSON Schemas committed for external tooling
src/arga_twins_benchmark/  Compiler, CLI adapter, runner contracts, and evaluation contracts
tests/                     Unit, contract, integration, conformance, and gold tests
runs/                      Gitignored immutable experiment artifacts
```

## Quick start

```bash
uv sync --group dev
uv run arga-bench catalog validate benchmark
uv run arga-bench compile blocking_code_review_v1_github_clean_001 -o /tmp/arga-scenario.json
uv run arga-bench scenarios save blocking_code_review_v1_github_clean_001
uv run arga-bench scenarios save-experiment development_pilot_48_v1
uv run arga-bench provision --help
uv run pytest
```

Authenticate once with `arga login`, or set `ARGA_API_KEY` for the benchmark wrapper's isolated temporary CLI config. See [running experiments](docs/running-experiments.md) for the exact lifecycle.

## Episode lifecycle

```text
validate manifest
  -> compile a named Scenario with task description and exact seed_config
  -> save or reuse the Scenario by content hash through arga CLI
  -> start twin run through arga CLI and persist its run ID immediately
  -> poll CLI status until ready (deployment + seeding)
  -> capture canonical baseline through trusted provider readers
  -> give only provider URLs/credentials to candidate adapter
  -> invoke candidate separately
  -> capture final state and grade required/forbidden predicates
  -> retain artifacts
  -> teardown through arga CLI and confirm the exact run is terminal with zero exposed twins
  -> keep the saved Scenario
```

The current Arga status contract confirms control-plane terminal state, not
completion of the asynchronous VM cleanup job. The harness binds that evidence
to the exact run ID and never silently treats a different or missing run as
clean. If a create response is lost before its run ID is persisted, resume is
quarantined until the configured TTL plus a five-minute grace period.

The saved Scenario is durable catalog metadata: its `name` is human-readable, its `description` contains the concrete task, and its `seed_config` is copied from checked-in seed files. `Scenario.prompt` remains unset so Arga cannot generate or repair fixture state from prose. The candidate still receives `prompt.txt` separately for each episode.

See the [design proposal](docs/design-proposal.md), [architecture](docs/architecture.md), [task catalog](docs/task-catalog.md), [48-task matrix](docs/task-matrix.md), [agent scorecard](docs/scorecard.md), [roadmap](docs/roadmap.md), [task authoring](docs/task-authoring.md), [evaluation contract](docs/evaluation-contract.md), [experiment execution](docs/running-experiments.md), [repeated-run analysis](docs/analyzing-repeated-runs.md), [candidate-safe surface](docs/candidate-safe-surface.md), and [security model](docs/security-model.md).

Completed suites can be checked offline with `scripts/audit_suite.py`; the audit makes no provider or Arga calls.
Preserved baseline state, final state, provider traces, and structured model output can be passed through the
full deterministic grader without reprovisioning twins:

```bash
uv run arga-bench grade-suite runs/<suite-run-id> \
  --root benchmark \
  --output runs/<suite-run-id>/semantic-grade.json
```

The derived grade is written separately from immutable execution artifacts and records SHA-256 hashes of every
input it consumed. Unsupported or incomplete canonical evidence is reported as `invalid_grader`, never converted
into an agent failure or guessed Task Success result. Add `--fail-on-incomplete` when a CI job must require a
fully gradeable matrix.

Analyze a completed semantic grade and its preserved traces without network access:

```bash
uv run arga-bench analyze-suite \
  runs/<suite-run-id>/semantic-grade.json \
  --suite-dir runs/<suite-run-id> \
  --root benchmark \
  --json-output runs/<suite-run-id>/repeated-analysis.json \
  --markdown-output runs/<suite-run-id>/repeated-analysis.md
```

The aggregate JSON and Markdown include model/task/family/variant/provider-role outcomes, repeat stability, fixed-seed task-cluster bootstrap intervals, paired model differences, official-doc use, call-error counts, endpoint-discovery/probing indicators, and redundant-call diagnostics. They omit trace bodies, request paths, non-official URLs, credentials, raw GraphQL, trace-error text, and fingerprints. See [repeated-run analysis](docs/analyzing-repeated-runs.md) for metric definitions.
