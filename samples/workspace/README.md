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

All five samples retain earlier passing hosted candidate episodes with preserved traces and confirmed teardown. Subsequent attempts are retained too: WKS-03 first failed by using superseded terms; its final-deployment mixed API/browser attempt reached the required provider state but stopped on a browser-driver timeout before submitting its final report, its one permitted exact-Scenario retry completed with the same 20-seat/USD8,400 business error and no prohibited side effects. That completed failure is retained and is not retried. WKS-02 passes on the final Sheets deployment. WKS-04 exposed malformed Gmail send acceptance; that unsafe result remains preserved, and fresh revalidation after the #1150 fix passes every required outcome and safety assertion. WKS-05 passes a documented same-state regrade: the original verifier mistook an empty internal collection index for a resource mutation. Its valid comment GET/PATCH calls exposed missing GitHub handlers; #1150 fixes those routes and fresh WKS-05 revalidation passes. WKS-01's unaffected passing result is preserved. These are functional trials, not a model comparison or exhaustive browser coverage. All completed candidate runs have confirmed teardown. Candidate reports are retained in [the sample PR](https://github.com/ArgaLabs/arga-twins-benchmark/pull/14).

| Task | Preserved passing episode | Evidence |
| --- | --- | --- |
| WKS-01 | Pass after deployed GitHub requested-reviewer/read-state fix | `wks-01-github-read-fix-candidate-2026-09-24.json` |
| WKS-02 | Pass on revised contract, including both protected attendees | `wks-02-reviewed-contract-candidate-2026-09-24.json` |
| WKS-03 | Pass with explicit customer-only recipient policy and required owner review | `wks-03-explicit-recipient-candidate-2026-09-24.json` |
| WKS-04 | Pass after deployed GitHub read-state fix and reviewed fact normalization | `wks-04-github-read-fix-candidate-2026-09-24.json` |
| WKS-05 | Mixed browser/API pass: 21 API calls, five documentation lookups, 14 Chrome actions and one codec call | `wks-05-browser-candidate-2026-09-24.json` |

An earlier September 24 revalidation passes WKS-01, WKS-02, WKS-04 and WKS-05; each chose APIs despite having browser tools, and all five runs were torn down. WKS-04 initially failed because the grader rejected “mitigation has been drafted but is NOT yet approved.” After correcting that equivalent-phrase matcher, the same captured state passes all thirteen assertions. The original failure and regrade are both preserved in `wks-04-revalidation-2026-09-24.json`; no candidate was rerun. Regrading all five captured attempts leaves every other outcome unchanged.

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

PR #1144 adds native Sheets basic filters, range sorting and condition-dependent value choices. All 489 Sheets/Workspace regressions and the full contract matrix pass. Desktop/390px Chrome checks cover filtering, sorting, value search/counts, cancellation, reload and protected cells. Disposable live Sheets fixtures verified blank-last sorting, relative/absolute formulas, filter-region inference and saved-condition choice narrowing. PR #1144 passed CI, merged as `ca16789b`, and passed both production deployments. Fresh hosted Chrome desktop/390px flows and native protected-state assertions pass; the validation run was torn down. See `sheets-filter-controls.json`, `sheets-filter-choices.json` and `sheets-filters-production-2026-09-24.json`.

PR #1146 adds relative-date presets and formula-based date conditions, tied to the controllable twin clock and spreadsheet time zone. All 545 Sheets/Workspace/Scenario regressions, the contract matrix and final focused/Chrome desktop/390px checks pass. Date-window choices, exact-date input, cancellation and reload match the checked provider flows; native protected state is unchanged. PR #1146 passed its current CI workflows and merged as `6a17ead0`; both production deployment workflows passed. A fresh hosted Scenario-clock check and Chrome desktop/390px date-filter, exact-formula, cancellation and reload checks pass with protected state preserved and teardown confirmed. See `sheets-date-filters.json` and `sheets-dates-production-2026-09-24.json`.

PR #1147 adds native data-validation rule editing, list/range dropdowns, checkbox mouse/keyboard input, help text, warning/rejection and relative predicate formulas. It preserves queued valid edits, single-choice dropdowns and blank unchecked values. The reviewed combined regression run has 584 passes; 139 final focused tests (overlapping), Ruff, Pyright and bounded Chrome desktop/390px/native-state checks pass. PR #1147 passed CI, merged as `82b34341`, and both production workflows passed. Fresh hosted desktop/390px rule, dropdown, checkbox, warning/rejection and relative-formula checks pass native-state assertions. The browser replay found an incorrect invalid marker after rejected edits; PR #1149 passed full CI and both production workflows, merged as `64c8c14b`, and passed a fresh hosted desktop queued-write replay. Successful and injected-failed preceding writes restore the correct committed value and validity marker. Native snapshots confirm only H3 changed from 35 to 45; protected state is unchanged and teardown is confirmed. A later fresh hosted 390×844 replay also passes the queued successful/failed-write, rejection, marker and reload checks, with protected state preserved and teardown confirmed. PR #1153 fixes initial invalid-marker rendering before focus after reload; 439 local regressions and desktop/390px native-state checks pass, including virtual-grid scrolling and broken-source isolation. Full current-head CI passes (5,307 twin and 2,106 backend tests); it merged as `3ad66f36` and both production workflows passed. Fresh hosted desktop/390px initial-load, reload, horizontal-scroll and sheet-switch checks pass before cell focus, with complete captured native state unchanged and teardown confirmed. See `sheets-initial-validation-marker-2026-09-24.json`. See `sheets-validation-controls.json`, `sheets-validation-production-2026-09-24.json` and `sheets-queued-validation-production-2026-09-24.json`. Colored/multiple-selection chips, full clipboard/structural-reference behavior and exhaustive validation-condition fidelity remain open.

PR #1124 adds Gmail compose windows and More options. The native Print/afterprint check now passes: two native previews were inspected and cancelled, temporary print frames were removed, and the complete native state remained unchanged. All 217 Gmail regressions and current-head CI passed; the PR merged as `94ab5965`. Both production workflows passed. Fresh hosted desktop/390px compose windows, menu keyboard/cancellation, fullscreen reload and native rich-draft/attachment preservation pass. A fresh hosted run now passes two native Print preview/cancel cycles, including keyboard activation and deferred afterprint frame cleanup. Complete native state remains equal and teardown is confirmed. No print job or PDF save was submitted. See `gmail-compose-window.json` and `gmail-compose-production-2026-09-24.json`.

Remaining work includes complete route/control inventories and reference/mobile audits, settings/admin/billing/analytics/AI and collaboration surfaces, GitHub advanced review/Projects/Actions, Gmail inline composer and remaining menus, Calendar access requests/side apps, Docs headers/suggesting/table positioning, Sheets filter views/charts/protection/undo, Notion workspace/guest/advanced database editing, Linear notifications/uploads/realtime, and Stripe reporting/admin. Physical-mobile coverage and complete deployed-image attestation remain open. `readiness.json` keeps all eight frontend gates pending.

PR #1150 fixes malformed Gmail send acceptance and native GitHub comment read/edit/delete/list gaps exposed by later candidate runs. It passed full CI (5,268 twin and 2,106 backend tests), merged as `f82d437e`, and both production workflows passed. Fresh hosted MIME/comment conformance and WKS-04/WKS-05 candidate revalidation pass with teardown confirmed. Both candidates chose APIs while browser tools remained available; these trials do not establish browser-agent coverage. PR #1151 adds shared native issue/PR conversation controls, including create/edit/delete, copy and quote, cancellation and error recovery. Its 383 local regressions and complete current-head CI pass (5,275 twin tests and 2,106 backend tests). It merged as `8b40448d` and both production workflows passed. Fresh hosted desktop and 390px issue/PR comment checks pass with exact protected-state comparison and teardown confirmed. Full review/admin/editor and physical-touch parity remain open. See `mail-comment-fidelity-2026-09-24.json` and `github-conversation-controls-2026-09-24.json`.

PR #1155 adds native header/footer/footnote editing, segment-aware save/recovery and tab isolation. Its export review fix retains segments across ten formats, with native DOCX headers/footers and linked footnotes. Local browser checks, 23 export regressions and 379 broader Docs/Drive/Workspace checks pass. CI (5,362 twin and 2,106 root tests), both production deployments and fresh hosted native/20-format-variant export checks pass; protected state is unchanged and teardown is confirmed. Hosted GUI/mobile checks remain pending while the host is locked. Page-exact PDF rendering and broader Docs fidelity remain open. See `docs-document-segments-2026-09-24.json`.

PR #1156 adds Linear native comment threads, author editing, copy links and resolution/reopening. Scenario graph validation now rejects malformed relationships before any reset, and introspection exposes supported thread fields and arguments. All 164 Linear regressions and bounded local desktop/390px flows pass. Full CI (5,392 twin and 2,106 root tests), both production deployments and fresh hosted native/form checks pass, with protected state preserved and teardown confirmed. Hosted GUI/mobile checks remain pending while the host is locked. See `linear-comment-threads-2026-09-24.json`.

PR #1158 adds native filter-view validation/preview and effective color filtering with 509 passing local Sheets/shared Workspace regressions. Filter-view frontend controls, sorted-view editing and browser checks remain unfinished; it is not deployed. See `sheets-filter-views-colors-2026-09-24.json`.

## Package validation

The current evidence includes a clean locked install and a later full run with 1,194 benchmark tests passing (26 skipped), candidate/relay/outcome regressions and 137 outcome regressions and 12 package-selected checks on the latest source (overlapping with the full suite). Input scans contain no live credentials or image files. Historical invocation results are retained in `evidence/regression-log-summaries.json`; overlapping test counts are not additive. Later source changes must refresh hashes and relevant checks before release. No final ZIP has been created.

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

The current sample suite passes 1,191 tests with 26 skips at grader revision `9f7b369`; changed-file Ruff/Pyright pass. Repository-wide Pyright has the same 1,196 diagnostics as the unchanged baseline; see `evidence/approval-grader-regressions-2026-09-24.json`. Historical checks remain labeled in `package-preflight-2026-09-22.json`. No final ZIP has been generated.
