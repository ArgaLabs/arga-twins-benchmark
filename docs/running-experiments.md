# Running experiments

## What runs where

Arga runs the twins, not the candidate agent. The benchmark runner uses the Arga CLI to create the fixture, then launches or calls the candidate through an independent adapter. The candidate can use any provider REST, GraphQL, SDK, or advertised MCP interface needed to complete the task.

## Prerequisites

Use an `arga` version containing the audited `test-runner scenarios list/import` and `twin-runs create/status/reset/teardown` JSON contracts. The catalog currently pins seed expectations to `validation-server@1aa60e0768adc4dcbccf932bdf2efe93917b5007` and was audited against `arga-cli@c88d5f160343e79b1de9ba5554e856825c566edc`.

```bash
export ARGA_API_URL=https://api.argalabs.com
```

Authenticate either way:

```bash
arga login
arga whoami
```

Or supply `ARGA_API_KEY` to `arga-bench`; its CLI adapter writes the key to a mode-`0600` temporary CLI home for subprocesses and removes it on close. The current upstream CLI does not itself read `ARGA_API_KEY`, so direct manual `arga` commands still require `arga login`.

## Save benchmark Scenarios

Save one instance with a readable name, its concrete task in `description`, and its exact checked-in `seed_config`:

```bash
export ARGA_API_KEY='<supplied-key>'

uv run arga-bench scenarios save \
  blocking_code_review_v1_github_clean_001
```

Save all 48 instances selected by the development experiment:

```bash
uv run arga-bench scenarios save-experiment \
  development_pilot_48_v1 \
  > /tmp/arga-bench-pilot-scenarios.json

jq -e '.experiment_id == "development_pilot_48_v1" and (.scenarios | length == 48)' \
  /tmp/arga-bench-pilot-scenarios.json
```

Serialized invocations are idempotent, and `save-experiment` saves its instances serially in manifest order. The compiler adds a `content-sha256:*` tag, and the wrapper uses `arga test-runner scenarios list --tag ... --json` before importing. It reuses one exact match, imports a new Scenario when there is no match, validates the returned and persisted record, and fails on duplicate or conflicting matches. Each result reports `scenario_id` and whether it was newly `created`. Do not run concurrent save commands for the same Arga account: Scenario tags are not server-side uniqueness constraints, although the post-import check detects the resulting duplicate.

`Scenario.prompt` stays unset. The concrete task is descriptive metadata only; Arga seeds solely from `seed_config`, and the runner sends `prompt.txt` separately to the candidate.

## Benchmark provisioning command

The implemented wrapper compiles, saves or reuses the Scenario, provisions, checks `status == ready`, and splits control data from agent data. Saving first is optional because `provision` invokes the same content-hash lookup:

```bash
export ARGA_API_KEY='<supplied-key>'

uv run arga-bench provision \
  blocking_code_review_v1_github_clean_001 \
  --control-output runs/manual/control.json \
  --candidate-output runs/manual/candidate-access.json
```

Give the agent the instance's `prompt.txt` and only `candidate-access.json`. Keep `control.json` in the trusted runner; both files are written mode `0600`. During development, reset or clean up through the wrapper:

```bash
uv run arga-bench reset runs/manual/control.json
uv run arga-bench cleanup runs/manual/control.json
```

These commands themselves invoke the Arga CLI; they contain no direct Arga HTTP client.

`cleanup` tears down the twin run but deliberately keeps the saved Scenario. A later provision can reuse the same Scenario while still creating a fresh twin run for trial isolation.

## Manual single episode

This lower-level sequence invokes `arga` directly and therefore requires a prior `arga login`; the wrapper's temporary `ARGA_API_KEY` configuration applies only to `arga-bench` commands. Use the `arga-bench provision/reset/cleanup` flow above when API-key-only authentication is desired.

Compile the named Scenario document from exact checked-in seeds. It includes `name`, `description`, `twins`, `seed_config`, and tags, but deliberately has no `prompt` field:

```bash
uv run arga-bench compile \
  blocking_code_review_v1_github_clean_001 \
  --output /tmp/arga-bench-scenario.json
```

Save it through the wrapper, then provision entirely through the Arga CLI:

```bash
uv run arga-bench scenarios save \
  blocking_code_review_v1_github_clean_001 \
  > /tmp/arga-bench-saved-scenario.json

SCENARIO_ID=$(jq -er '.scenario_id' /tmp/arga-bench-saved-scenario.json)

arga twin-runs create \
  --api-url "$ARGA_API_URL" \
  --twins github \
  --scenario-id "$SCENARIO_ID" \
  --ttl 60 \
  --wait \
  --timeout 600 \
  --json > /tmp/arga-bench-run.json

RUN_ID=$(jq -er '.run_id' /tmp/arga-bench-run.json)
jq -e '.status == "ready" and .is_public == true' /tmp/arga-bench-run.json
```

The harness transforms the full run response into a candidate-access document containing only public provider base URLs, ordinary twin-native environment values, and optional MCP URLs. It then sends that document plus `prompt.txt` to the chosen agent adapter. Do not pass the raw run JSON to the agent.

After candidate completion, trusted provider readers capture final state and execute the instance's registered verifier. Tear down the ephemeral twin run in a `finally` block; do not delete the saved Scenario:

```bash
arga twin-runs teardown --api-url "$ARGA_API_URL" "$RUN_ID" --json
```

For local iteration only, restore the captured seed baseline with:

```bash
arga twin-runs reset --api-url "$ARGA_API_URL" "$RUN_ID" --json
arga twin-runs status --api-url "$ARGA_API_URL" "$RUN_ID" --json
```

Use a fresh twin run for each scored repetition so agent memory, caches, and failed cleanup cannot cross trials. Reusing a saved Scenario is safe because each new twin run is seeded from the same immutable `seed_config`; never reuse a mutated twin run as a scored repetition.

## Candidate adapter contract

The runner passes a typed request, not a mandated HTTP endpoint:

```json
{
  "prompt": "<contents of prompt.txt>",
  "trial_id": "opaque random identifier",
  "current_time": "2030-05-14T12:00:00Z",
  "principal": "benchmark_user",
  "tenant": "acme",
  "provider_access": {
    "github": {
      "base_url": "https://pub-example",
      "env": {"GITHUB_TOKEN": "twin-native-token"}
    }
  }
}
```

Adapters may translate this into a local process environment, container config, SDK call, or hosted `/invoke` request. The agent's final text/JSON, exit status, latency, and trusted usage telemetry are retained.

## Batch protocol

For each agent/configuration:

1. Pin the agent commit, model, system prompt, tool catalog, budgets, runner commit, CLI version, and catalog fingerprint.
2. Randomize episode order with the experiment seed.
3. Run every selected instance five times on fresh twins.
4. Re-run infrastructure-invalid trials without counting them as agent failures.
5. Report Task Success, Unsafe Action, Over-Refusal, Recovery/Idempotency, and Provider Invariance separately.
6. Bootstrap confidence intervals by semantic family, not individual trial.

Save the experiment's Scenario set before a run:

```bash
uv run arga-bench scenarios save-experiment development_pilot_48_v1
```

The intended end-to-end batch command surface is:

```text
arga-bench run <experiment> --agent <adapter-config>
arga-bench resume <suite-run-id>
arga-bench grade <trial-id>
arga-bench report <suite-run-id>
arga-bench conformance
```

Catalog validation, fingerprinting, Scenario compilation, durable Scenario saving, and single-instance provisioning/reset/cleanup are implemented today. Until `arga-bench run` lands, execute experiments by provisioning each selected instance, invoking the candidate with `prompt.txt` plus `candidate-access.json`, recording the result, and tearing down the run with `arga-bench cleanup`. End-to-end candidate invocation, grading, and batch orchestration remain Milestone 1 deliverables.

## Current CLI gaps

Uniform hidden grading still needs `arga twin-runs diagnostics <run-id> --json` and a stable twin-run logs command. Until those exist, a task pack must capture baseline/final state through ordinary provider APIs with trusted credentials. Public twin hosts also need an admin-path denylist before adversarial public scoring. These are release gates, not reasons to weaken verifier ground truth.
