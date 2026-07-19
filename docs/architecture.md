# Architecture

## Boundary

The repository is a client-side compiler, runner, and evaluator. Benchmark semantics stay here. Arga supplies deterministic provider twins, and every Arga control-plane operation goes through the installed CLI.

```mermaid
flowchart LR
    C["Catalog: world + template + binding + variant"] --> P["Compiler"]
    P --> S["Exact seed-only Scenario JSON"]
    S --> CLI["Authenticated Arga CLI"]
    CLI --> T["Provisioned provider twins"]
    R["Episode runner"] --> CLI
    R --> I["External candidate adapter"]
    T --> I
    T --> V["Trusted provider readers + verifier"]
    V --> O["Result artifacts + report"]
```

The runner never calls an Arga server endpoint. Its infrastructure adapter invokes `arga ... --json`. Candidate and verifier traffic goes to provisioned provider APIs; those are twin data-plane operations rather than Arga control-plane operations.

## Package responsibilities

- `specs`: Pydantic source of truth for catalog and result contracts.
- `catalog`: load, validate, compile, and fingerprint declarative content. No network calls.
- `arga_cli`: typed subprocess wrapper for Scenario import/delete and twin-run create/status/reset/teardown.
- `agents`: candidate invocation protocol. An adapter may use a process, container, hosted endpoint, or SDK.
- `runner`: durable episode state machine, retry policy, cancellation, and teardown.
- `evaluation`: canonical snapshots, state diffs, predicates, collateral-damage detection, and harm classification.
- `providers`: twin-specific state readers, canonicalization, and semantic normalization.
- `taskpacks`: registered trusted verifiers, gold solutions, and negative controls.
- `reporting`: metric aggregation, confidence intervals, and exports.

Catalog manifests refer to registered verifier and gold IDs. They never execute arbitrary import paths.

## Candidate connection contract

The control process retains the complete CLI response. The candidate receives a whitelist only:

```json
{
  "github": {
    "base_url": "https://pub-example",
    "mcp_url": null,
    "env": {"GITHUB_TOKEN": "twin-native-token"}
  }
}
```

It never receives `admin_url`, `proxy_token`, `seed_results`, Arga authentication, fixtures, expected state, or verifier code.

## Episode state machine

```text
PLANNED
  -> MANIFEST_VALIDATED
  -> SCENARIO_REGISTERED
  -> TWIN_RUN_REQUESTED
  -> TWINS_READY_AND_SEEDED
  -> BASELINE_CAPTURED
  -> INVOCATION_STARTED
  -> INVOCATION_FINISHED
  -> FINAL_CAPTURED
  -> GRADED
  -> ARTIFACTS_COMMITTED
  -> TEARDOWN_REQUESTED
  -> COMPLETE
```

On the audited Arga contract, `twin-runs create --wait` reaches `ready` only after deployment, exact Scenario seeding, and post-seed health checks. The wrapper still checks the JSON status because CLI exit success alone does not distinguish a failed run or wait timeout. Teardown runs in `finally`; mutation-capable candidate invocation is never retried blindly.

## Artifact contract

Each episode writes an immutable directory:

```text
runs/<suite-run-id>/<trial-id>/
  manifest.lock.json
  scenario.json
  candidate-access.redacted.json
  baseline/
  final/
  agent-output.json
  trace.jsonl
  result.json
  infrastructure.log
```

The lock records hashes for template, instance, world, binding, seed, verifier, runner, agent commit, CLI version, and twin images. CLI argv/stdout/stderr are retained only after credential and token redaction.
