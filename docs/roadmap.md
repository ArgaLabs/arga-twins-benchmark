# Roadmap

## Milestone 0 — Contracts and 48-instance development catalog

Deliverables:

- Pydantic models and committed JSON Schemas.
- Catalog validation, deterministic fingerprints, and exact-seeded Scenario compiler.
- Durable named Scenarios with task descriptions, content-hash reuse, and no `Scenario.prompt`.
- Typed subprocess adapter for the audited Arga CLI lifecycle.
- Twelve semantic families and 48 specified development instances.
- Controlled-clock, authorization, required/forbidden mutation, budget, and output contracts.

Exit criterion: every artifact validates and fingerprints reproducibly without live infrastructure.

## Milestone 1 — CLI-only vertical slice

Deliverables:

- Runner that saves or reuses a Scenario and manages twin runs through the Arga CLI only.
- Pluggable candidate adapter that receives sanitized provider endpoints and invokes the agent separately.
- Durable state machine, immutable artifacts, and credential-redacted CLI traces.
- Canonical state diffing, deterministic predicates, and infrastructure-invalid classification.
- Gold solution and known-wrong controls for one inspectable task.
- Ten fresh-provision gold passes.

Start with `blocking_code_review_v1_github_clean_001`, then add one safety pair and one provider-transfer pair.

Exit criterion: `arga-bench run` executes and grades an episode end to end, always tears down, and reproduces the same relevant state across ten provisions.

## Milestone 2 — Conformance and small scientific pilot

Promote a representative 16-instance slice only after each selected task passes live seed-shape, provider-read, reset, gold, negative-control, and collateral-damage tests. Add batch execution, paired seeds, fixed budgets, randomized ordering, resume support, and clustered reporting.

Exit criterion: two agents can be compared on the same 16-instance matrix with complete traces, hard safety failures, and infrastructure-invalid episodes excluded from agent metrics.

## Milestone 3 — 48-instance calibration release

Promote the checked-in 12 templates × four variants = 48 instances after Milestone 2 passes. Rotate entities and distractors into private variants, and add controlled failure schedules where providers support deterministic injection.

## Milestone 4 — Main benchmark

Expand to 60 templates and 240 instances, then run provider-transfer, tool-catalog, scaffolding, interface, asynchronous-recovery, and multi-tenant experiments.

## Immediate implementation order

1. Add `ARGA_API_KEY` precedence, `twin-runs diagnostics`, and stable `twin-runs logs` upstream to the Arga CLI.
2. Implement the runner against fake CLI and fake candidate adapters.
3. Add the first live GitHub vertical slice and provider canonicalizer.
4. Conformance-test all seed shapes and deterministic verifier predicates.
5. Run gold and known-wrong controls across ten fresh provisions per selected task.
6. Complete public twin data/control-plane isolation before scoring untrusted agents.
