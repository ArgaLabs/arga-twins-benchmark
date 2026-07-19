# AGENTS.md

This repository inherits the durable workspace instructions in `/Users/tonghx/AGENTS.md`.

## Benchmark invariants

- Keep the agent-facing task prompt outside Arga `Scenario.prompt`.
- Use the authenticated Arga CLI for every Arga control-plane operation. Do not call Arga server endpoints directly.
- Run candidate agents separately and pass them only ordinary provider API URLs and twin-native credentials.
- Provision twins from exact, checked-in `seed_config` files. Never generate scored fixtures with an LLM.
- Keep task semantics provider-neutral; provider details belong in bindings, seeds, canonicalizers, and verifiers.
- Grade canonical state, required outcomes, and forbidden mutations. Request traces are diagnostic evidence, not the primary ground truth.
- Treat critical unsafe actions as hard failures that partial credit cannot offset.
- Record infrastructure validity separately from agent outcome.
- Never expose seeds, expected state, verifier code, gold solutions, Arga credentials, or twin admin/reset routes to candidate agents.
- Keep private-test and challenge instances outside the public repository.
- Use a fixed controlled clock and explicit dates in scored fixtures.

## Development

```bash
uv sync --group dev
uv run arga-bench catalog validate benchmark
uv run pytest
uv run ruff check .
uv run pyright
```
