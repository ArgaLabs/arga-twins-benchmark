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

The implemented wrapper compiles, saves or reuses the Scenario, starts the run, persists its returned ID before waiting, polls `arga twin-runs status` until `status == ready`, and splits control data from agent data. Saving first is optional because `provision` invokes the same content-hash lookup:

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

`cleanup` tears down the twin run, then polls CLI status until that exact run ID is terminal with zero twins exposed by the status response. It deliberately keeps the saved Scenario. A later provision can reuse the same Scenario while still creating a fresh twin run for trial isolation.

This is control-plane evidence, not proof that Arga's asynchronous VM cleanup worker has finished deleting infrastructure. The current status API does not surface that worker-completion fact. The harness therefore records the evidence precisely as returned, binds it to the expected run ID, and relies on the run TTL as the resource-lifetime backstop.

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
  --json > /tmp/arga-bench-run-start.json

RUN_ID=$(jq -er '.run_id' /tmp/arga-bench-run-start.json)

# Poll with `arga twin-runs status --json` until the run is ready or terminal.
arga twin-runs status \
  --api-url "$ARGA_API_URL" \
  "$RUN_ID" \
  --json > /tmp/arga-bench-run.json

jq -e '.status == "ready" and .is_public == true' /tmp/arga-bench-run.json
```

Do not hide run creation inside a long `--wait` subprocess: a cancellation can otherwise occur after server creation but before the runner durably records the run ID. The wrapper handles the start/status loop and timeout automatically.

The harness transforms the full ready response into a candidate-access document containing only public provider base URLs, ordinary twin-native environment values, and optional MCP URLs. It then sends that document plus `prompt.txt` to the chosen agent adapter. Do not pass the raw run JSON to the agent.

After candidate completion, trusted provider readers capture final state and execute the instance's registered verifier. Tear down the ephemeral twin run in a `finally` block; do not delete the saved Scenario:

```bash
arga twin-runs teardown --api-url "$ARGA_API_URL" "$RUN_ID" --json
arga twin-runs status --api-url "$ARGA_API_URL" "$RUN_ID" --json
```

The teardown response may initially say `cleaning_up`. Do not treat that as complete; require a later CLI status for the same run ID of `cancelled`, `expired`, `torn_down`, or another clean terminal status with `twins: {}`. This confirms the public control-plane contract only; it does not claim asynchronous VM deletion has completed.

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

## Exact prompt ledger

Generate the exact system and user text sent to every model before launching the matrix:

```bash
uv run arga-bench prompts development_pilot_48_v1 \
  --output runs/prompt-ledger-48x3.json \
  --markdown-output runs/prompt-ledger-48x3.md
```

The ledger has 144 entries: 48 instances for each of `claude-opus-4-8`, `claude-fable-5`, and `gpt-5.6-sol`. All three receive identical text for a given instance; only the model/API thinking configuration differs.

## Run one canary

Put credentials in an ignored mode-`0600` `.env`:

```dotenv
ARGA_API_KEY=<arga-key>
ANTHROPIC_API_KEY=<anthropic-key>
OPENAI_API_KEY=<openai-key>
```

Then run one exact model against a fresh twin:

```bash
chmod 600 .env

uv run arga-bench run-instance \
  blocking_code_review_v1_github_clean_001 \
  --model claude-opus-4-8 \
  --env-file .env \
  --ttl 60
```

## Run and resume the 48 × 3 matrix

```bash
uv run arga-bench run-matrix development_pilot_48_v1 \
  --models claude-opus-4-8,claude-fable-5,gpt-5.6-sol \
  --root benchmark \
  --output-root runs \
  --env-file .env \
  --repeats 1 \
  --concurrency 4 \
  --ttl 60
```

The command prints a `suite_run_id`. Resume that exact suite after interruption or a transient Arga/model-provider error:

```bash
uv run arga-bench run-matrix development_pilot_48_v1 \
  --models claude-opus-4-8,claude-fable-5,gpt-5.6-sol \
  --root benchmark \
  --output-root runs \
  --env-file .env \
  --repeats 1 \
  --concurrency 4 \
  --ttl 60 \
  --suite-run-id <suite-run-id>
```

Resume preserves completed or substantive terminal outcomes, confirms prior cleanup through the Arga CLI, archives retryable or interrupted attempts, and provisions a fresh twin before replaying an infrastructure-invalid trial. Cleanup evidence must name the exact persisted run ID. When a create may have succeeded but its response was interrupted before the ID became durable, the attempt is quarantined until its configured TTL plus five minutes; the resulting lease-expiry evidence is recorded with the archived attempt. It never retries mutations in place.

Each suite contains its manifest, exact prompt ledger, summary, one directory per active trial result, and immutable archived attempts. Candidate traces contain only calls routed to the provisioned provider endpoints.

Audit the saved evidence without making any network calls:

```bash
uv run python scripts/audit_suite.py \
  runs/<suite-run-id> \
  --prompt-ledger runs/prompt-ledger-48x3.json \
  --minimum-tool-calls 6
```

The audit checks exact response-model identity, prompt hashes, disabled fallback, provider destinations, control-plane avoidance, call counts, cleanup run-ID identity, and state-grade completeness. Add `--fail-unless-scoring-ready` in CI when an incomplete matrix or incomplete semantic state grade must fail the job.

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

`run-instance`, `run-matrix`, exact prompt ledgers, candidate invocation, trusted raw baseline/final capture, trace/output grading, cleanup, attempt archival, and safe resume are implemented. Full semantic state grading is still fail-closed: current results report `state_grade_complete: false` until every declared snapshot is hydrated into complete canonical resources and semantic mutations. Do not publish the preliminary trace/output result as final Task Success.

## Current CLI gaps

Uniform hidden grading still needs `arga twin-runs diagnostics <run-id> --json` and a stable twin-run logs command. Until those exist, a task pack must capture baseline/final state through ordinary provider APIs with trusted credentials. Public twin hosts also need an admin-path denylist before adversarial public scoring. These are release gates, not reasons to weaken verifier ground truth.
