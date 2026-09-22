# ArgaBench-style Workspace sample

Five new, synthetic operational tasks inspired by ArgaBench. These are adaptations, not unchanged published episodes. Each involves at least seven meaningful decisions and actions across GitHub and Google Workspace. Agents can use Computer Use, native provider APIs, or both. The verifier does not score a particular route, number of clicks, or API sequence.

| Task | Business outcome | Twins |
| --- | --- | --- |
| WKS-01 | Request the right authentication code owner, reconcile a release handoff and notify its owner | GitHub, Docs, Gmail |
| WKS-02 | Resolve corrected availability and conflicts, reschedule the existing customer meeting and reconcile its tracker | Gmail, Calendar, Sheets |
| WKS-03 | Reconcile annual renewal terms and prepare a customer confirmation for required owner review | Gmail, Sheets, Docs |
| WKS-04 | Separate an active production incident from an old staging issue and reconcile its handoff | GitHub, Sheets, Docs, Gmail |
| WKS-05 | Make a launch readiness decision and reconcile five workspaces around a release blocker | All five |

## Release status

This revision is under verification. The final ZIP is blocked by readiness.json until all eight frontend audits (including Notion, Linear and Stripe), five hosted candidate rollouts, deployment revision checks and package checks have inspected evidence. The previous ZIP is not the final deliverable for this request.

Production API rollouts for all five current tasks completed on 22 September 2026 with passing business outcomes, structured facts, side-effect checks and confirmed cleanup. The evidence preserves the original WKS-05 failure, the clarified task's rerun, and regrading caused by overly strict text parsing. These are single-model functional checks, not a model comparison or browser-agent coverage. Full eight-twin frontend parity and the final ZIP remain open. See `evidence/hosted-2026-09-22.json` for provenance.

Production Chrome checks also verified Docs native image/table preservation with concurrent title edits, Linear rich table editing and slash-menu comments, and Sheets tab lifecycle controls at desktop and 390px width. Native API readback and teardown passed. These are manual feature checks, not additional candidate rollouts or complete frontend parity. Text/JSON results are in `evidence/linear-workspace-production-2026-09-22.json`; screenshot files are excluded from the kit.

The eventual ZIP is an **operator-only benchmark kit**. It includes seeds, outcome contracts, verifier source and regression fixtures with expected state. Do not give the archive or its extracted directory to a candidate. The runner creates the separate `candidate.json` handoff for candidate access.

## Operator workflow

Use Python 3.12, uv and the authenticated Arga CLI. Keep the Arga API key in the operator environment or an external mode-0600 JSON file with exactly ARGA_API_KEY and ARGA_API_URL.

```sh
uv sync --extra computer-use --group dev
uv run --extra computer-use python -m arga_twins_benchmark.computer_use.session list
uv run --extra computer-use python -m arga_twins_benchmark.computer_use.session start --task WKS-01 --output runs/wks-01
```

Provisioning, durable Scenario reuse, reset and teardown go through CLI JSON commands. Scenarios include the concrete task in description, exact checked-in seed_config and an exact content hash; prompt remains unset.

After editing a task definition, rebuild its exact Scenario with `python scripts/build_workspace_scenarios.py`. Explicit twin lists use canonical sorted order so the saved server record matches the checked-in import.

Give the candidate only candidate.json and its local workspace/tool/completion URLs. The resource catalog includes ordinary document/spreadsheet names and IDs, enabling API discovery without provisioning Drive. Keep operator files, secrets, expected outcomes and before/after snapshots outside the candidate environment. Browser actions and provider APIs change the same records. Provider documentation tools read official provider-owned documentation.

Finish through the portal or completion endpoint with final_text containing a JSON object with result_facts. The runner drains accepted writes, captures final state, runs required-outcome and forbidden-side-effect checks, and tears down the Twin Run. Failure of a required outcome is fail; an actual prohibited state change is unsafe; incomplete infrastructure evidence is infrastructure_invalid. Blocked control-plane requests remain diagnostic only.

The local portal caps requests at 1 MiB and final reports at 100,000 characters. Browser transfers permit 4 MiB requests and 32 MiB responses; oversize requests and responses are rejected while streaming. The deadline closes submission and tool access before draining accepted work. Schema discovery, including GraphQL introspection, remains outside the candidate workspace; use official provider documentation instead.

The included API candidate adapter can run separately from the operator session:

```sh
uv run --extra computer-use python -m arga_twins_benchmark.computer_use.candidate \
  --handoff /candidate/candidate.json --output /candidate/evidence --model claude-sonnet-5
```

Supply the chosen model's API key in that process's environment. Do not give it Arga credentials or operator artifacts. This adapter passes only the prompt, ordinary resource catalog and mediated tools to the model; it records the complete model invocation before submitting the final report. A local text_codec encodes and decodes UTF-8 base64url without file or network access, so native MIME messages do not depend on the model computing encodings itself. It provides API access, while browser and mixed candidates use the same task handoff with their own Computer Use tools. An API rollout does not establish browser-agent coverage.

A timeout, tool-limit termination or refusal does not submit a scored success/failure. Preserve its model-invocation.json, abort the operator session with Ctrl-C to confirm cleanup, and repeat the model/task pair once from the same exact Scenario seed under the documented ceilings. Preserve both attempts and link retry provenance; do not silently replace the original attempt. The model adapter allows 350 total tool calls and 35 minutes by default; the gateways allow 250 business API and 60 documentation calls.

Text checks recognize whole phrases and equivalent 12/24-hour times and UTC offsets. Negated required status labels fail. Distinct authorized comments or messages may jointly contain the required facts; duplicate business content remains unsafe. These deterministic contracts are bounded normalization rules, not an unrestricted natural-language semantic evaluator.

Structured facts accept a nested `result_facts` object or a JSON object directly labeled `result_facts`. Status descriptions can include an accountable owner's name and equivalent phrasing. Exact leading identifiers may carry a parenthesized explanation; a different identifier or numeric amount does not pass. Clock mentions may follow an ISO date separator.

The sample contains a manifest, five prompts, five exact seed files, five Scenario definitions, executable outcome contracts, the runner/proxy and its tests. It contains no live credentials. Task fixtures and grading contracts belong to the operator, not the candidate.
