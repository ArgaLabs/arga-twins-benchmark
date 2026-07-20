# Task authoring

## Authoring unit

A semantic template describes the business workflow independently of provider APIs. A world supplies controlled time, identities, tenants, permissions, and shared identifiers. A binding maps functional roles to concrete twins. A variant changes one controlled factor. Compilation produces an immutable instance.

```text
world + template + binding + variant -> instance -> episode bundle
```

## Instance layout

```text
benchmark/instances/<split>/<instance-id>/
  instance.yaml
  prompt.txt
  seed/<twin>.json
  verify.yaml
```

`prompt.txt` is the only task instruction sent to the candidate. The compiler also copies that concrete task text into the saved Scenario's descriptive metadata so operators can identify it in Arga; this does not make it a seeding prompt. Seed files, verification rules, expected state, and meaningful hidden identifiers remain in the trusted runner zone.

## Required task properties

- One unambiguous user instruction.
- Exact checked-in seed files for every selected twin.
- Fixed clock and explicit dates.
- Principal, tenant, authorization, and action limits when relevant.
- Relevant target and realistic distractors.
- Expected semantic state.
- Explicit allowed and forbidden mutations.
- Critical requirements and diagnostic partial-credit weights.
- Registered verifier and gold solution IDs.
- Negative controls for wrong target, omitted action, and collateral mutation.
- A hidden dependency graph containing at least six meaningful agent steps.
- At least six semantically necessary provider tool interactions. Each interaction must retrieve distinct evidence, perform an authorized state transition, or confirm the resulting state; redundant calls do not count.
- A structured deterministic verifier with trusted snapshot queries, canonical state assertions, a default-deny mutation policy, and candidate trace constraints.
- No prose-only failure schedule. The current Arga Scenario import path does not install benchmark `failure_schedule` metadata into twins, so the scored catalog requires ordinary successful provider interactions until fault rules become part of the exact seed contract.

The prompt should state the user's goal, authority, and safety boundaries. Policies, target selection evidence, computed answers, and distractors belong in provider state. Do not reveal the gold target, derived decision, exact evidence, or precomputed schedule in the prompt merely to make grading easier.

`instance.yaml` records the hidden task graph under `complexity.agent_steps` and the independently necessary provider interactions under `complexity.tool_interactions`. This metadata is never passed to the candidate. Each interaction belongs to exactly one step. Catalog validation rejects fewer than six steps or calls, dangling or multiply linked interactions, dependency cycles, a causal path shorter than six steps, unknown provider roles, and a minimum that exceeds the tool-call budget. The grader assigns trace events to required interactions subject to that graph, which makes evidence-before-write and write-before-confirmation ordering executable rather than documentary.

`verification.yaml.deterministic` is the machine-readable grading source of truth:

- `snapshot_queries` define trusted provider reads and canonicalizers.
- `state_assertions` identify exact final resources, fields, and cardinalities.
- `mutation_policy` requires expected deltas and denies everything not explicitly allowed. State and mutation matchers must use non-empty selectors, expected fields, and exact canonical field allowlists; vacuous matchers are invalid.
- `trace_policy` requires at least six candidate calls, constrains expected calls, denies control-plane paths, and allowlists every mutating request. Every declared tool-interaction ID must map to a required trace-rule ID, those rules must require the full call minimum, and at least six distinct provider path/operation/signature combinations must be necessary. GraphQL rules must identify their normalized operation, required calls are assigned to distinct trace events, and every allowed write must have a finite cardinality bound. Rules count only successful `2xx` responses by default; override `status_min` and `status_max` only when a specific non-success response is itself necessary evidence. Use `allow_missing_status` only for an exact timeout-after-commit write with an independently verified mutation and post-state.

The current scored 48 use only successful `2xx` interactions. The schema and evaluator retain explicit status ranges for future fault-injection tasks, but those tasks cannot enter a scored experiment until their failure rules are encoded in `seed_config`, applied by the twin Scenario runner, and covered by conformance tests. A sentence in `failure_schedule` is documentation, not an executable fixture.

When one rule requires several resources, set `distinct_by: path` (or `path_and_operation`) so repeatedly reading one object cannot satisfy the count. Prefer exact hidden resource paths for policy, approval, and manifest evidence. A broad detail-route regex is not sufficient when the verifier knows which seeded records are independently necessary.

Human-readable expected/allowed/forbidden lists remain useful review documentation, but they cannot substitute for the deterministic block.

Every scored task asks the candidate for a concise JSON report and uses a critical `structured_facts` output contract. Require only the semantic facts that prove the agent reached the right conclusion, such as the selected record, decision, or no-op reason. Treat the required facts as a subset so harmless extra fields are allowed, and never require one exact sentence or byte-for-byte response. `mode: none` remains available only for unscored smoke and conformance checks.

## Seeding rule

The compiler emits an Arga Scenario with a human-readable `name`, a `description` containing the concrete task, the selected `twins`, exact checked-in `seed_config`, and a deterministic content-hash tag. It must omit `Scenario.prompt`; prompt-based fixture generation or repair is not a reproducible seed source.

The runner uses `arga test-runner scenarios list --tag ... --json` and `arga test-runner scenarios import --json` to save each compiled Scenario once and reuse it by content hash. Changing the task, fixture, or other fingerprinted instance-bundle content produces a new hash and therefore a new saved Scenario; cleanup tears down only the episode's twin run.

## Variant rule

All variants derived from one semantic template stay in one split. Hidden variants should rotate IDs, names, dates, ordering, and distractors so correctness requires solving the task rather than memorizing a fixture.

## Release gate

Before an instance can be scored:

1. Ten resets produce equivalent canonical relevant state.
2. The gold solution passes repeatedly.
3. Known-wrong solutions fail their intended assertions.
4. Unrelated mutations are detected.
5. Safety harms are categorized.
6. Task wording and ground truth receive human review.
