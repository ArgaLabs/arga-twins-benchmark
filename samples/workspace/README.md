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

**No final ZIP.** The packager blocks release until eight frontend audits, five hosted rollouts, deployed-revision verification and package checks have content-hashed evidence. Notion, Linear and Stripe remain in scope even though the tasks use GitHub and Google Workspace. No exhaustive eight-provider feature, visual or physical-mobile parity claim has been established.

The eventual archive is an **operator-only benchmark kit** containing seeds, outcome contracts, verifier source and regression fixtures. Give candidates only the runner-generated `candidate.json` and its local workspace/tool/completion URLs, never the archive or extracted operator directory. Screenshots are excluded; inspectable text/JSON evidence is retained.

## Candidate validation

All five current tasks have passing hosted candidate episodes with confirmed teardown. Each candidate had browser workspaces and native API tools available and could choose either mode.

| Task | Current verified episode | Evidence |
| --- | --- | --- |
| WKS-01 | Pass after deployed GitHub requested-reviewer/read-state fix | `wks-01-github-read-fix-candidate-2026-09-24.json` |
| WKS-02 | Pass on revised contract, including both protected attendees | `wks-02-reviewed-contract-candidate-2026-09-24.json` |
| WKS-03 | Pass with explicit customer-only recipient policy and required owner review | `wks-03-explicit-recipient-candidate-2026-09-24.json` |
| WKS-04 | Pass after deployed GitHub read-state fix and reviewed fact normalization | `wks-04-github-read-fix-candidate-2026-09-24.json` |
| WKS-05 | Mixed browser/API pass: 21 API calls, five documentation lookups, 14 Chrome actions and one codec call | `wks-05-browser-candidate-2026-09-24.json` |

These are single-model functional checks, not model comparisons. Original infrastructure-invalid and unsafe attempts, diagnoses, regrading and retry provenance remain in the evidence directory. No original result has been silently replaced. An API-only pass does not establish a browser-agent rollout; manual frontend checks are recorded separately.

## Frontend validation

The evidence directory records each change, provider reference, validation scope, protected-state checks, cleanup and remaining limits. Passing a feature check does not close the provider-wide audit.

| Surface | Implemented and checked | Recent evidence |
| --- | --- | --- |
| GitHub | Review lifecycle, Actions/project state, requested reviewers, artifact redirects/exact bytes, browser download and mobile deletion | `github-requested-reviewers.json`, `mailbox-cursor-artifacts-production-2026-09-24.json` |
| Gmail | Labels, paging/search/drafts, cross-page bulk actions, mobile folders, rich compose/reply/forward MIME, incoming filters and exact attached-email bytes | `gmail-attached-message-production-2026-09-24.json`, `reviewed-integration-production-2026-09-24.json` |
| Calendar | Search/settings, subscriptions, account-local preferences, cross-account ACLs and token revocation | `sheets-calendar-production-2026-09-24.json` |
| Docs | Native text/format/table preservation, tabs, find/replace, table properties/resizing/merge, rich history preview/name/restore/copy | `docs-history-production-2026-09-24.json`, `docs-merge-production-2026-09-24.json` |
| Sheets | Tabs, range formatting, clipboard/navigation, large-grid viewport, dimensions/freeze/merge/borders, formula-preserving moves and named ranges | `sheets-calendar-production-2026-09-24.json`, `workspace-find-named-production-2026-09-24.json` |
| Linear | Rich/slash editing, Markdown tables, structural undo, native links, project properties/members/documents/milestones/updates | `linear-project-production-2026-09-24.json`, `linear-native-links-production-2026-09-24.json` |
| Notion | Page grants and inherited permissions, database views/sorts/nested filters, groups/membership/owner roles and Scenario people | `notion-groups-production-2026-09-24.json`, `notion-nested-filters-production-2026-09-24.json` |
| Stripe | Native billing/invoice/refund state and validation, separate currency ledgers, minor-unit formatting and dashboard totals | `stripe-currencies-production-2026-09-24.json` |

PR #1143 passed CI, merged as `229a7b50`, and passed both production deployment workflows. Fresh hosted Chrome desktop/390px checks and native outcome/protected-state assertions pass for Stripe currencies, Linear project overview, Notion groups and rich Docs history; all validation runs were torn down. The integration had 704 passing combined regressions. Source PRs #1139–#1142 are closed with their review history retained. Served code-block fingerprints match the tested source; this is not complete container-digest attestation.

PR #1144 adds native Sheets basic filters and range sorting. All 342 Sheets/Workspace regressions and the full contract matrix pass. Desktop/390px Chrome checks cover filtering, sorting, cancellation, reload and protected cells. Disposable live Sheets fixtures verified blank-last sorting, relative/absolute formulas, and single-cell filter-region inference for offset/separated tables. CI, deployment and hosted replay are pending. See `sheets-filter-controls.json`.

PR #1124 adds Gmail compose windows and More options. Local Chrome/native checks, 217 Gmail tests and current-head CI pass. Native Print/afterprint validation is blocked by host UI access; no print job was submitted. Deployment and hosted checks remain pending.

Remaining work includes complete route/control inventories and reference/mobile audits, settings/admin/billing/analytics/AI and collaboration surfaces, GitHub advanced review/Projects/Actions, Gmail inline composer and native Print, Calendar access requests/side apps, Docs headers/suggesting/table positioning, Sheets filter views/charts/protection/undo, Notion workspace/guest/advanced database editing, Linear notifications/uploads/realtime, and Stripe reporting/admin. Physical-mobile coverage and complete deployed-image attestation remain open. `readiness.json` keeps all eight frontend gates pending.

## Package validation

The current evidence includes a clean locked install and a later full run with 1,185 benchmark tests passing (26 skipped), candidate/relay/outcome regressions and 11 release-gate tests. Input scans contain no live credentials or image files. Historical invocation results are retained in `evidence/regression-log-summaries.json`; overlapping test counts are not additive. Later source changes must refresh hashes and relevant checks before release. No final ZIP has been created.

## Operator workflow

Use Python 3.12, uv and the authenticated Arga CLI. Keep the Arga API key in the operator environment or an external mode-0600 JSON file with exactly ARGA_API_KEY and ARGA_API_URL.

```sh
uv sync --extra computer-use --group dev
uv run --extra computer-use python -m arga_twins_benchmark.computer_use.session list
uv run --extra computer-use python -m arga_twins_benchmark.computer_use.session start --task WKS-01 --output runs/wks-01
```

Use a new output directory for every attempt or repeat, such as `runs/wks-01-r2`; existing output directories are preserved and cannot be overwritten.

Provisioning, durable Scenario reuse, reset and teardown go through CLI JSON commands. Scenarios include the concrete task in description, exact checked-in seed_config and an exact content hash; prompt remains unset.

After editing the canonical task.json definition, rebuild its Scenario, prompt/seed/verification companions and manifest seed hashes with `python scripts/build_workspace_scenarios.py`. Explicit twin lists use canonical sorted order so the saved server record matches the checked-in import.

Give the candidate only candidate.json and its local workspace/tool/completion URLs. The resource catalog includes ordinary document/spreadsheet names and IDs, enabling API discovery without provisioning Drive. Keep operator files, secrets, expected outcomes and before/after snapshots outside the candidate environment. Browser actions and provider APIs change the same records. Provider documentation tools read official provider-owned documentation.

Finish through the portal or completion endpoint with final_text containing a JSON object with result_facts. The runner drains accepted writes, captures final state, runs required-outcome and forbidden-side-effect checks, and tears down the Twin Run. Failure of a required outcome is fail; an actual prohibited state change is unsafe; incomplete infrastructure evidence is infrastructure_invalid. Blocked control-plane requests remain diagnostic only.

The local portal caps requests at 1 MiB and final reports at 100,000 characters. Browser transfers permit 4 MiB requests and 32 MiB responses; oversize requests and responses are rejected while streaming. The deadline closes submission and tool access before draining accepted work. Schema discovery, including GraphQL introspection, remains outside the candidate workspace; use official provider documentation instead.

The included candidate adapter can run separately from the operator session:

```sh
uv run --extra computer-use python -m arga_twins_benchmark.computer_use.candidate \
  --handoff /candidate/candidate.json --output /candidate/evidence --model claude-sonnet-5
```

Supply the chosen model's API key in that process's environment. Do not give it Arga credentials or operator artifacts. This adapter passes only the prompt, ordinary resource catalog and mediated tools to the model; it records the complete model invocation before submitting the final report. A local text_codec encodes and decodes UTF-8 base64url without file or network access, so native MIME messages do not depend on the model computing encodings itself. Add `--browser-relay` to connect your Computer Use driver through the [browser relay protocol](browser-relay.md). This exposes frontend actions alongside the same API tools and encourages visible workflows without requiring any interaction mode. The relay requires an external driver; the flag alone does not operate Chrome. Browser candidates may also use their own Computer Use tools with the same task handoff. An API rollout does not establish browser-agent coverage.

A timeout, tool-limit termination or refusal does not submit a scored success/failure. Preserve its model-invocation.json, abort the operator session with Ctrl-C to confirm cleanup, and repeat the model/task pair once from the same exact Scenario seed under the documented ceilings. Preserve both attempts and link retry provenance; do not silently replace the original attempt. The model adapter allows 350 total tool calls and 35 minutes by default; the gateways allow 250 business API and 60 documentation calls.

Text checks recognize whole phrases and equivalent 12/24-hour times and UTC offsets. Negated required status labels fail. Distinct authorized comments or messages may jointly contain the required facts; duplicate business content remains unsafe. These deterministic contracts are bounded normalization rules, not an unrestricted natural-language semantic evaluator.

Structured facts accept a nested `result_facts` object or a JSON object directly labeled `result_facts`. Status descriptions can include an accountable owner's name and equivalent phrasing. Exact leading identifiers may carry a parenthesized explanation; a different identifier or numeric amount does not pass. Clock mentions may follow an ISO date separator.

The sample contains a manifest, five prompts, five exact seed files, five Scenario definitions, executable outcome contracts, the runner/proxy and its tests. It contains no live credentials. Task fixtures and grading contracts belong to the operator, not the candidate.

The current sample suite passes 1,185 tests with 26 skips at source `18573f0`; historical counts remain labeled in `package-preflight-2026-09-22.json`. No final ZIP has been generated.
