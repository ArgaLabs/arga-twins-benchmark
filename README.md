# Arga Twins Benchmark

A provider-general, stateful benchmark for evaluating whether agents can complete useful work, respect authorization boundaries, recover from operational failures, and transfer the same workflow across service providers.

This repository owns benchmark semantics, agent execution, grading, and experiment orchestration. The authenticated Arga CLI provisions and manages deterministic service twins; candidate agents run separately and call the resulting provider APIs.

## Current status

The development catalog contains 12 semantic task families with four variants each: 48 specified prompts, exact twin seeds, authorization envelopes, and deterministic verification manifests. They are benchmark candidates, not yet a public leaderboard. Each must pass live twin conformance, gold-solution, negative-control, and isolation gates before promotion to a scored split.

## Principles

1. Author semantic workflows independently of provider names.
2. Store exact seeds; never use natural-language scenario generation for scored runs.
3. Evaluate final state and collateral mutations rather than one prescribed tool trajectory.
4. Pair unsafe cases with nearly identical authorized cases.
5. Report capability, safety, robustness, transfer, and infrastructure validity separately.
6. Keep candidate data-plane access separate from hidden grader and Arga CLI access.

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
uv run arga-bench provision --help
uv run pytest
```

Authenticate once with `arga login`, or set `ARGA_API_KEY` for the benchmark wrapper's isolated temporary CLI config. See [running experiments](docs/running-experiments.md) for the exact lifecycle.

## Episode lifecycle

```text
validate manifest
  -> compile exact seed-only Scenario JSON
  -> import Scenario through arga CLI
  -> create twin run with --wait through arga CLI
  -> require status=ready (deployment + seeding)
  -> capture canonical baseline through trusted provider readers
  -> give only provider URLs/credentials to candidate adapter
  -> invoke candidate separately
  -> capture final state and grade required/forbidden predicates
  -> retain artifacts
  -> teardown twin run and delete Scenario through arga CLI
```

See the [design proposal](docs/design-proposal.md), [architecture](docs/architecture.md), [task catalog](docs/task-catalog.md), [agent scorecard](docs/scorecard.md), [roadmap](docs/roadmap.md), [task authoring](docs/task-authoring.md), [evaluation contract](docs/evaluation-contract.md), [experiment execution](docs/running-experiments.md), and [security model](docs/security-model.md).
