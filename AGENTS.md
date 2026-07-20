# AGENTS.md

This repository inherits the durable workspace instructions in `/Users/tonghx/AGENTS.md`.

## Benchmark invariants

- Keep the agent-facing task prompt outside Arga `Scenario.prompt`.
- Use the authenticated Arga CLI for every Arga control-plane operation. Do not call Arga server endpoints directly.
- Save durable Scenarios with a human-readable name and the concrete task in `description`; reuse exact content-hash matches and keep them when twin runs are torn down.
- Run candidate agents separately and pass them only ordinary provider API URLs and twin-native credentials.
- Provision twins from exact, checked-in `seed_config` files. Never generate scored fixtures with an LLM.
- Keep task semantics provider-neutral; provider details belong in bindings, seeds, canonicalizers, and verifiers.
- Grade canonical state, required outcomes, and forbidden mutations. Request traces are diagnostic evidence, not the primary ground truth.
- Treat critical unsafe actions as hard failures that partial credit cannot offset.
- Record infrastructure validity separately from agent outcome.
- Never expose seeds, expected state, verifier code, gold solutions, Arga credentials, or twin admin/reset routes to candidate agents.
- Keep private-test and challenge instances outside the public repository.
- Use a fixed controlled clock and explicit dates in scored fixtures.
- Every instance in the scored 48-episode pilot must require at least six semantically necessary agent steps and six provider tool interactions. Redundant calls added only to inflate difficulty do not count.
- Every scored instance must include structured, executable snapshot, state, mutation, and trace verification. Prose verification is explanatory only and cannot be the grading source of truth.
- Every scored instance must require critical structured result facts; harmless extra fields are allowed, but a wrong decision or classification cannot pass.
- Do not use prose-only `failure_schedule` entries in scored experiments. Fault behavior must be installed through exact Scenario seed data and proven by twin conformance before a non-success call can be required.
- Keep one-action API checks outside the scored 48; use them only for smoke or conformance testing.

## Development

```bash
uv sync --group dev
uv run arga-bench catalog validate benchmark
uv run pytest
uv run ruff check .
uv run pyright
```
