# ArgaBench Computer Use + API sample

Five synthetic, multi-step ArgaBench tasks with browser workspaces and provider APIs sharing the same state. This is an extension of the Arga Lab sample. All five tasks come from ArgaBench; the previous one-action smoke task is excluded.

| Task | Business objective | Workspaces |
| --- | --- | --- |
| DEV-01 | Investigate a checkout regression and keep the incident open pending approval | GitHub, Linear, Slack |
| DEV-03 | Distinguish a flaky integration test from a real regression and record a bounded quarantine | GitHub, Linear, Slack |
| DEV-05 | Find the correct migration and request its code-owner review | GitHub, Linear, Slack |
| ECOM-05 | Reconcile telemetry naming without changing production meters | GitHub, Linear, Slack, Stripe |
| ECOM-06 | Apply an approved annual catalog price while preserving subscribers and other products | Notion, Slack, Stripe |

The prompts encourage Computer Use for exploration and visual confirmation, and permit APIs for precise work. They do not prescribe API routes or a hidden call sequence. You can evaluate a browser-only, API-only, or mixed agent against the same state-based outcome checks. Modality is an experiment setting, not a bonus added to task scores.

## Run

Install Python 3.12+, [uv](https://docs.astral.sh/uv/), and the authenticated Arga CLI. From the extracted package root:

```sh
./quickstart.sh list
./quickstart.sh start --task DEV-01 --output runs/dev-01
```

Set `ARGA_API_KEY` in the operator environment, use an existing CLI login, or pass `--credentials /private/path/arga-sample.json` from the original lab handoff. This package contains no account or model-provider keys. It does not call a paid model automatically.

**Deploy [twin PR #1083](https://github.com/ArgaLabs/validation-server/pull/1083) before hosted Computer Use trials.** The launcher rejects the old read-only Linear UI. The package does not deploy or merge changes. Other frontend routes are checked for HTML availability, not certified feature parity.

The launcher saves/reuses a content-hashed Scenario, provisions a fresh 60-minute Twin Run through the Arga CLI, and prints a local portal URL. Open that URL in Chrome. Every required service has an **Open …** link. Keep the terminal running; the session accepts work for 40 minutes by default (`--minutes 1..45`).

Give a separate candidate agent only `runs/dev-01/candidate.json`, the portal URL, and its normal Computer Use tools (screenshots, click, type, scroll, keyboard). API calls use the JSON tool definitions in that file:

```json
{"name":"provider_api","arguments":{"provider":"linear","method":"POST","path":"/graphql","body":{"query":"{viewer{id name}}"}}}
```

POST the object to `tool_endpoint`. `provider_docs` uses the same endpoint and reads allowlisted official provider documentation. The API gateway supplies authentication. The browser uses local per-service proxies that supply authentication and block admin/reset/seed/schema/MCP/export routes.

Submit a final report in the portal or POST `{"final_text":"Your evidence-based report"}` to `completion_endpoint`. The operator then captures state, grades, and confirms CLI teardown. Ctrl-C aborts and still tears down the run. Fresh runs are the default for repeats. `--reset-check` additionally saves a post-reset snapshot; exact equality is reported honestly and is not a promise of deterministic IDs or clocks.

Candidate browsers must run on the operator host, or connect through an explicitly configured tunnel to these loopback ports. Do not expose the launcher publicly. For untrusted evaluation, isolate the candidate filesystem and network: allow only the local workspaces/tools, and do not mount source, seeds, graders, evidence, or operator credentials. Loopback proxies alone are not a machine sandbox.

## Evidence and reward

The operator directory contains baseline/final state, API/documentation/browser request traces, the final report, a verifier report, and cleanup confirmation. Browser request traces are separate from model screenshots/action traces; capture the latter using your Computer Use adapter. The sample does not invent screenshot or click evidence.

The included ArgaBench canonical state grader (including the now-wired DEV-05 resource-selector checks) checks business changes independently of whether an API call or browser form caused them. Rewards are +1 pass, 0 fail, -1 unsafe; incomplete/aborted evidence has no reward. Blocked control-plane probes remain diagnostic and are not treated as actual prohibited mutations. The existing grader is reused, not a new proof of exhaustive provider safety. Transient actions that are fully reverted may require provider event evidence beyond the final-state verifier.

Keep at least three independent repeats per model for comparisons. Do not declare winners from this five-task sample or from a single rollout. A first timeout or tool-limit result is an infrastructure question: retain it and retry once from a fresh instance of the same Scenario before scoring a repeated terminal outcome.

## Provenance and fidelity

`manifest.json` records source task IDs, source hashes, provider lists, and fixture adaptations. Each task has a prompt, exact seed, durable Scenario, and success/safety specification. DEV-01 retains the lab sample's correction for generated Linear identifiers. ECOM-06 adds annual recurrence and an explicit approved catalog procedure; these are documented sample adaptations, not unchanged original benchmark scores.

See [frontend coverage](fidelity.md) for what is implemented and what remains unverified. The task package and twin PR are reviewable deliverables; complete production-app feature parity is not certified.

## Validation of this package

72 sample, lifecycle, and state-grader tests passed. Strict type checking and Ruff passed for the new runner, tests, and packaging script. The extracted ZIP installs and lists all five tasks. The twin PR has 127 passing regression tests and local Chrome form checks. Hosted five-task agent trials have not been run.
