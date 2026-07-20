# Arga Twins Benchmark

A provider-general, stateful benchmark for evaluating whether agents can complete useful work, respect authorization boundaries, recover from operational failures, and transfer the same workflow across service providers.

This repository owns benchmark semantics, agent execution, grading, and experiment orchestration. The authenticated Arga CLI provisions and manages deterministic service twins; candidate agents run separately and call the resulting provider APIs.

## Current status

The development catalog contains 12 semantic task families with four variants each: 48 scored episodes with exact twin seeds, authorization envelopes, explicit six-or-more-step evidence graphs, and executable deterministic verification manifests. One-action API checks are separate smoke/conformance material and do not count toward the scored 48. The catalog remains a benchmark candidate rather than a public leaderboard until every episode passes live twin conformance, gold-solution, negative-control, and isolation gates.

## Principles

1. Author semantic workflows independently of provider names.
2. Store exact seeds; never use natural-language scenario generation for scored runs.
3. Evaluate final state and collateral mutations rather than one prescribed tool trajectory.
4. Pair unsafe cases with nearly identical authorized cases.
5. Report capability, safety, robustness, transfer, and infrastructure validity separately.
6. Keep candidate data-plane access separate from hidden grader and Arga CLI access.
7. Require at least six semantically necessary provider interactions in every scored episode; never pad call counts with redundant reads.
8. Default-deny unlisted mutations and grade exact canonical state plus the trusted candidate-call ledger.
9. Require the correct structured decision as well as correct provider state.
10. Score only fixture behavior installed by exact Scenario seeds; prose-only fault schedules are not executable evidence.

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
  -> create twin run with --wait through arga CLI
  -> require status=ready (deployment + seeding)
  -> capture canonical baseline through trusted provider readers
  -> give only provider URLs/credentials to candidate adapter
  -> invoke candidate separately
  -> capture final state and grade required/forbidden predicates
  -> retain artifacts
  -> teardown twin run through arga CLI; keep the saved Scenario
```

The saved Scenario is durable catalog metadata: its `name` is human-readable, its `description` contains the concrete task, and its `seed_config` is copied from checked-in seed files. `Scenario.prompt` remains unset so Arga cannot generate or repair fixture state from prose. The candidate still receives `prompt.txt` separately for each episode.

See the [design proposal](docs/design-proposal.md), [architecture](docs/architecture.md), [task catalog](docs/task-catalog.md), [48-task matrix](docs/task-matrix.md), [agent scorecard](docs/scorecard.md), [roadmap](docs/roadmap.md), [task authoring](docs/task-authoring.md), [evaluation contract](docs/evaluation-contract.md), [experiment execution](docs/running-experiments.md), and [security model](docs/security-model.md).
