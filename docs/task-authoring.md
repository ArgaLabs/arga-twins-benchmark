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
