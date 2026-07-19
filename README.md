# Arga Twins Benchmark

A provider-general, stateful benchmark for evaluating whether agents can complete useful work, respect authorization boundaries, recover from operational failures, and transfer the same workflow across service providers.

This repository owns benchmark semantics and experiment orchestration. Arga provides the service twins, deterministic environments, sandbox deployment, diagnostics, logs, and teardown.

## Current status

Milestone 0: contracts and repository scaffold.

The first goal is not a large leaderboard. It is one trustworthy episode that can be reset, executed, graded, and reproduced end to end. We will expand to the proposal's 12-template, 48-instance calibration release only after the runner, evaluator, and control-plane boundary pass their release gates.

## Principles

1. Author semantic workflows independently of provider names.
2. Store exact seeds; never use natural-language scenario generation for scored runs.
3. Evaluate final state and collateral mutations rather than one prescribed tool trajectory.
4. Pair unsafe cases with nearly identical authorized cases.
5. Report capability, safety, robustness, transfer, and infrastructure validity separately.
6. Keep candidate data-plane access separate from hidden grader and admin access.

## Repository map

```text
benchmark/                 Worlds, templates, bindings, instances, and experiments
docs/                      Architecture, contracts, security model, and roadmap
schemas/                   Generated JSON Schemas committed for external tooling
src/arga_twins_benchmark/  Compiler, Arga client, runner, evaluation, and reporting
tests/                     Unit, contract, integration, conformance, and gold tests
runs/                      Gitignored immutable experiment artifacts
```

## Quick start

```bash
uv sync --group dev
uv run arga-bench catalog validate benchmark
uv run arga-bench catalog fingerprint blocking_code_review_v1_github_clean_001
uv run pytest
```

The initial catalog contains two development instances:

- Gmail + Google Calendar meeting amendment reconciliation.
- GitHub blocking code review without repository mutation.

They are contract fixtures for Milestone 0. Live execution arrives in Milestone 1.

## Episode lifecycle

```text
validate manifest
  -> register exact scenario
  -> create sandbox
  -> wait for deployment readiness
  -> separately confirm seeding
  -> capture baseline
  -> invoke candidate /invoke
  -> capture final state
  -> grade required and forbidden predicates
  -> retain artifacts
  -> teardown
```

See the [original design proposal](docs/design-proposal.md), [architecture](docs/architecture.md), [roadmap](docs/roadmap.md), [task authoring](docs/task-authoring.md), [evaluation contract](docs/evaluation-contract.md), [experiment execution](docs/running-experiments.md), and [security model](docs/security-model.md).
