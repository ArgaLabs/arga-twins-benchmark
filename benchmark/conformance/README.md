# Development-pilot verifier conformance

This registry covers `development_pilot_48_v1`, not the active 40-task
ArgaBench v1 release.

Verifier conformance is a release gate, not an agent leaderboard track. A model
result is not rankable until the verifier for that exact instance has proved all
of the following:

1. Its declared gold final state passes.
2. Every declared negative control fails its intended hard predicate.
3. A semantically equivalent, non-reference provider trajectory reaches the
   same acceptable final state and passes even when route diagnostics differ.
4. Ten Arga CLI resets reproduce one canonical baseline.
5. A second independently provisioned run has the same canonical baseline.
6. An unrelated probe mutation is visible to the trusted state reader and the
   next reset restores the baseline.
7. The gold execution passes at least three independent repeats.
8. Teardown is confirmed through the Arga CLI.

The gate is fail-closed. Missing, stale, duplicate, orphaned, or contradictory
evidence keeps `leaderboard_ready=false`.

## Checked-in coverage

[`registry.json`](registry.json) is an explicit typed registry for all 48
development instances. They remain unscored until this gate passes. The
registry has a one-to-one relationship with:

- 48 `gold_solution_id` values declared by verification manifests;
- 144 `negative_control_ids` values declared by verification manifests; and
- 48 additional semantic-equivalence cases, one per gold case.

Each registry entry pins both the verifier fingerprint and complete instance
bundle fingerprint. Catalog changes therefore make the registry stale instead
of silently reusing old conformance evidence.

Regenerate the deterministic registry after intentional catalog changes:

```bash
uv run python scripts/regenerate_conformance_registry.py
uv run python scripts/regenerate_conformance_registry.py --check
```

The generator does not manufacture passing evidence. Newly discovered controls
remain `pending` unless an exact checked-in evaluator fixture is registered.

## What is proved today

The representative Stripe-clean bundle in
[`fixtures/stripe_price_normalization_clean.json`](fixtures/stripe_price_normalization_clean.json)
executes five cases:

- the gold final state;
- a semantically equivalent trajectory that intentionally misses the reference
  route checklist but reaches the same safe final state;
- replacement-price creation;
- mutation of the wrong price; and
- a correct target mutation plus collateral catalog mutation.

Those fixtures exercise trusted-state parsing, Stripe canonicalization,
baseline-relative state evidence, mutation mapping, default-deny safety, output
facts, and outcome-first grading. All five pass their conformance expectations.

They are synthetic state-evidence fixtures. They do **not** prove live provider
behavior, seeding, authorization semantics, or reset determinism. The other 235
evaluator cases are explicitly `pending`, and no live case/lifecycle evidence
is checked in. Consequently the benchmark is intentionally not
leaderboard-ready.

## Audit command

The default command exits nonzero unless the complete release gate passes:

```bash
uv run arga-bench conformance audit \
  --output runs/conformance-audit.json
```

Use `--allow-pending` only to inspect current development coverage:

```bash
uv run arga-bench conformance audit \
  --allow-pending \
  --output runs/conformance-audit.json
```

The machine-readable report separates:

- structural registry coverage;
- executable evaluator-fixture results;
- pending evaluator cases;
- live case observations;
- live reset/isolation evidence; and
- final leaderboard readiness.

## Live lifecycle hook

The lifecycle probe provisions, resets, and tears down exclusively through the
Arga CLI helpers. It never calls an Arga control-plane server endpoint directly.
Trusted snapshots are read from verifier-only twin surfaces returned in the
private CLI control record.

```bash
uv run arga-bench conformance live-reset-isolation \
  stripe_price_normalization_v1_stripe_clean_001 \
  --resets 10 \
  --output runs/conformance/stripe-clean-lifecycle.json
```

This CLI command proves repeated reset equality, an independently provisioned
baseline, and cleanup. It intentionally records
`mutation_visibility_proved=false`, because no generic safe mutation can stand
in for an instance-specific negative-control action. The Python lifecycle hook
accepts a provider-specific mutation probe; until one is supplied and its
mutation is observed and reset, the release audit remains closed.

## Required live case evidence

Live case records use protocol `arga-bench-live-case-conformance/1` and must pin
the current instance and verifier fingerprints. Each observation includes:

- exact case ID and repeat number;
- task-success and collateral-damage decisions;
- hard assertion results; and
- hashes of baseline state, final state, and provider trace.

Gold requires three passing repeats. Every negative control requires at least
one failing execution and must fail each registry-declared intended assertion.
The semantic-equivalence case requires at least one passing execution. Duplicate
repeats and unknown case IDs are rejected.

Live lifecycle records use protocol
`arga-bench-live-lifecycle-conformance/1`. Readiness requires ten reset hashes
equal to baseline, a distinct fresh-run baseline equal to the same hash, a
visible mutation hash different from baseline, restoration to baseline after
reset, and confirmed cleanup.

## Adding conformance for another verifier

1. Add evaluator evidence for its gold case.
2. Add one non-reference trajectory with the same correct and safe final state.
3. Add a fixture for each of its three declared negative controls, including
   the exact intended hard assertion IDs.
4. Change only those exact registry cases from `pending` to `executable`.
5. Run the audit and confirm every evaluator fixture passes.
6. Execute live gold/control/equivalence cases against freshly reset Scenario
   runs and save secret-safe evidence.
7. Run the CLI lifecycle probe with an instance-specific visible mutation
   probe.
8. Re-run the audit without `--allow-pending`.

Do not promote a task because its seed compiles or because one model happened
to receive the expected score. Conformance must establish the verifier
independently of candidate behavior.
