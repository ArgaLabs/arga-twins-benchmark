# Running experiments

## What runs where

Arga runs the twins, not the candidate agent. The benchmark runner uses the Arga CLI to create the fixture, then launches or calls the candidate through an independent adapter. The candidate can use any provider REST, GraphQL, SDK, or advertised MCP interface needed to complete the task.

## Prerequisites

Use an `arga` version containing the audited `twin-runs reset` command and Scenario JSON import contract. The catalog currently pins seed expectations to `validation-server@1aa60e0768adc4dcbccf932bdf2efe93917b5007` and was audited against `arga-cli@c88d5f160343e79b1de9ba5554e856825c566edc`.

```bash
export ARGA_API_URL=https://api.argalabs.com
```

Authenticate either way:

```bash
arga login
arga whoami
```

Or supply `ARGA_API_KEY` to `arga-bench`; its CLI adapter writes the key to a mode-`0600` temporary CLI home for subprocesses and removes it on close. The current upstream CLI does not itself read `ARGA_API_KEY`, so direct manual `arga` commands still require `arga login`.

## Benchmark provisioning command

The implemented wrapper compiles, imports, provisions, checks `status == ready`, and splits control data from agent data:

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

## Manual single episode

Compile exact checked-in seeds. The result deliberately has no `prompt` field:

```bash
uv run arga-bench compile \
  blocking_code_review_v1_github_clean_001 \
  --output /tmp/arga-bench-scenario.json
```

Import and provision entirely through the CLI:

```bash
arga test-runner scenarios import \
  --api-url "$ARGA_API_URL" \
  --file /tmp/arga-bench-scenario.json \
  --json > /tmp/arga-bench-import.json

SCENARIO_ID=$(jq -er '.id' /tmp/arga-bench-import.json)

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

After candidate completion, trusted provider readers capture final state and execute the instance's registered verifier. Clean up in a `finally` block:

```bash
arga twin-runs teardown --api-url "$ARGA_API_URL" "$RUN_ID" --json
arga test-runner scenarios delete --api-url "$ARGA_API_URL" "$SCENARIO_ID" --json
```

For local iteration only, restore the captured seed baseline with:

```bash
arga twin-runs reset --api-url "$ARGA_API_URL" "$RUN_ID" --json
arga twin-runs status --api-url "$ARGA_API_URL" "$RUN_ID" --json
```

Use a fresh twin run for each scored repetition so agent memory, caches, and failed cleanup cannot cross trials.

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

The intended command surface is:

```text
arga-bench run <experiment> --agent <adapter-config>
arga-bench resume <suite-run-id>
arga-bench grade <trial-id>
arga-bench report <suite-run-id>
arga-bench conformance
```

Only catalog validation, fingerprinting, Scenario compilation, and the typed Arga CLI adapter are implemented today. End-to-end batch execution remains a Milestone 1 deliverable.

## Current CLI gaps

Uniform hidden grading still needs `arga twin-runs diagnostics <run-id> --json` and a stable twin-run logs command. Until those exist, a task pack must capture baseline/final state through ordinary provider APIs with trusted credentials. Public twin hosts also need an admin-path denylist before adversarial public scoring. These are release gates, not reasons to weaken verifier ground truth.
