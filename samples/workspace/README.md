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

All five original production API episodes passed their then-current business outcomes, structured facts and side-effect checks on 22 September, with confirmed teardown. WKS-01, WKS-02 and WKS-04 now have reviewed prompts/contracts and updated seeds where needed; WKS-03 has an expanded semantic status matcher. WKS-02 now passes a fresh revised-contract production episode; the other affected gates remain pending. Historical results do not establish the revised tasks pass. They are single-model functional checks, not a model comparison. Original attempts and regrading provenance remain in `evidence/hosted-2026-09-22.json`.

Subsequent runs exposed browser tools alongside APIs without forcing either mode:

| Task | Preserved result |
| --- | --- |
| WKS-01 | Infrastructure-invalid after the allowed retry; not scored as task failure |
| WKS-02 | API-only pass on the allowed infrastructure retry |
| WKS-03 | Original unsafe result retained; the internal-reviewer recipient policy was ambiguous and has now been made explicit. No email was sent |
| WKS-04 | API-only pass after offline correction of the semantic result matcher; original evidence and verdict retained, no candidate rerun |
| WKS-05 | Mixed browser/API pass: 21 API calls, five official-documentation lookups, 14 Chrome actions and one codec call |

The 24 September reviewed-contract runs add these results: WKS-02 passes all ten assertions with both attendees unchanged; WKS-01 and WKS-04 expose a GitHub GET-state mutation fixed by PR #1137; WKS-04 also required recognition of the valid "not yet approved" mitigation wording. WKS-03 used one browser observation and APIs, then exposed the ambiguous reviewer-recipient policy. Original verdicts, diagnoses and cleanup are retained in `wks-*-reviewed-contract-candidate-2026-09-24.json`. The revised WKS-03 policy and fresh GitHub deployment still require reruns.

See the matching `evidence/wks-*-browser-candidate-*.json` files for complete provenance. WKS-05 establishes one complete hosted mixed-mode episode. Manual frontend checks are separate from candidate episodes.

## Frontend validation

The evidence directory records each change, validation scope, provider reference, protected-state checks, cleanup and limitations. Passing feature checks do not close a provider-wide audit.

| Surface | Implemented and checked | Principal evidence |
| --- | --- | --- |
| GitHub | Review lifecycle, Actions/project state, merge status, artifact native redirects and exact archive bytes, browser download and mobile deletion | `github-reference-audit-2026-09-22.json`, `mailbox-cursor-artifacts-production-2026-09-24.json` |
| Gmail | Label visibility, native paging/search/draft rows, cross-page bulk actions, mobile folders, rich MIME/attachments and post-send refresh | `gmail-production-2026-09-22.json`, `gmail-mailbox-production-2026-09-22.json`, `workspace-integration-production-2026-09-22.json`, `gmail-docs-production-2026-09-24.json`, `mailbox-cursor-artifacts-production-2026-09-24.json` |
| Calendar | Search/settings, shared subscriptions, account-local preferences, cross-account ACLs and existing-token revocation | `frontend-integration-production-2026-09-23.json`, `sheets-calendar-production-2026-09-24.json` |
| Docs | Native text/format/structure preservation, tab lifecycle, find/replace, table properties, fixed paper geometry and merge/unmerge | `docs-controls-production-2026-09-22.json`, `workspace-integration-production-2026-09-22.json`, `workspace-find-named-production-2026-09-24.json`, `docs-table-options-production-2026-09-24.json`, `docs-merge-production-2026-09-24.json` |
| Sheets | Tab lifecycle, range formatting, clipboard/navigation, large-grid viewport, dimensions/freeze/merge/borders, formula-preserving moves and named ranges | `sheets-range-production-2026-09-22.json`, `sheets-viewport-production-2026-09-22.json`, `sheets-frozen-production-2026-09-22.json`, `sheets-calendar-production-2026-09-24.json`, `workspace-find-named-production-2026-09-24.json` |
| Linear | Safe rich/slash editing, native Markdown tables, structural editing/undo, stable IDs and pointer-save persistence | `linear-workspace-production-2026-09-22.json`, `linear-table-production-2026-09-22.json`, `linear-pointer-save.json` |
| Notion | Person/group and inherited page grants, general-access expiry, native/UI enforcement | Provider-wide workspace/guest/settings/collaboration audit remains open |
| Stripe | Shared native invoice/subscription/refund state, totals, transition and refund validation | Provider-wide billing/settings/admin/reporting audit remains open |

Recent production checks passed for PR #1125 (Gmail mailbox refresh, empty Docs tab insertion and GitHub artifact API/browser paths) at `95e755a1`, and PR #1126 (Docs merge/unmerge) at `6c739745`. Both deployment workflows passed for each. Fresh hosted Chrome/native checks preserve protected records and confirm Twin Run teardown. Exact served code blocks are checked where recorded; this does not attest every container digest.

The latest contract review passes 185 focused runner/candidate/outcome tests, including negative cases for vague status text, falsely approved mitigation, and changed attendees. New local native-API reference fixtures pass; previous fixtures are retained and the weak ones are explicitly regraded as failures. See `workspace-contract-review.json`.

Recent implementation batches:

- PR #1127 combines Gmail incoming filters and Linear resource mentions/document editing. It passes 188 Gmail and 101 Linear regressions (289 combined), plus 772 Gmail contract checks. Chrome desktop/390px and native-state checks pass. Live Gmail fixtures confirmed insert filtering, malformed-query behavior and attached-email text matching; all disposable fixtures were cleaned up. CI passed and the PR merged as `45af59ee`; both production deployments passed. Hosted Linear mentions and mobile document edits pass with protected records unchanged and teardown confirmed. Hosted Gmail filter creation exposed a Scenario-owner scope bug, fixed in merged integration #1133 and now passing the fresh hosted replay. See `mentions-filter-production-2026-09-24.json`.
- PR #1128 adds Docs table border dragging. It passes 308 Docs/Workspace tests plus two additional title-status regressions and 18 focused resize/save-queue tests. Chrome desktop/390px, merged-edge and pending-edit/reload checks pass. The live provider comparison confirms column deltas. A follow-up in PR #1133 corrects row geometry using live provider measurements. Cancellation handler regressions pass, but native cancellation and physical-touch checks remain open. CI passed and the PR merged as `93f4c45d`; both production workflows passed; fresh hosted desktop and 390px row resizing/reload checks now pass. See `docs-table-resize.json` and `docs-table-resize-provider-reference.json`.
- PR #1129 isolates Linear Scenario seeding from demo records and preserves the Gmail mock owner's normal settings scopes. All 293 combined Gmail/Linear tests pass, including blank state, deterministic replay, reset and filter/settings coverage. Included in deployed integration #1133; fresh hosted validation passes.
- PR #1130 adds Notion table/list/board/gallery database views, filters, sorting, property visibility, grouping and row creation. All 74 Notion tests pass; Chrome desktop/390px and native readback confirm existing pages remain unchanged. Review fixes enforce disabled integration capabilities and native URL/email/phone filters, and reject unusable query changes before saving. Complex-property sorting remains open. Included in deployed integration #1133; fresh hosted validation passes. See `notion-database-views.json`.
- PR #1131 fixes duplicate-name Linear project URLs with stable IDs and preserves unique legacy aliases through renames. All 103 Linear tests pass. Included in deployed integration #1133; hosted in-app links resolve both duplicate-name projects correctly, but direct navigation to provider-style project paths returned 404 and remains open.
- PR #1132 preserves native Gmail reply/forward recipients, threading, rich MIME, inline images and attachments. All 193 Gmail tests pass, plus 12 focused tests after the final CID match refinement. Chrome desktop/390px and native-state checks pass; a disposable real Gmail fixture confirmed forward rich text and exact attachment bytes, with cleanup and no email sent. The provider inline composer layout still differs. Deployed integration #1133 passes hosted reply-all recipients, rich text, inline image bytes and filter delivery; attached-email forwarding failed and is addressed separately in PR #1135. See `gmail-reply-forward.json`.
- PR #1133 integrates the reviewed changes from #1129–#1132 and the Docs row-geometry correction. All 685 combined regressions pass, followed by 100 Notion tests for native filter-validation review fixes. Invalid API view filters are rejected atomically even for empty databases and nested branches. All checks passed, and the integration merged as `85b310a0`. Both production workflows passed. Fresh hosted Chrome/native checks pass for Docs row resizing, Notion database views/row creation, Linear in-app mentions/mobile editing, and Gmail filters/reply-all. The preserved report is a partial failure: Gmail forwarding dropped `reference.eml`, and Linear direct provider-style paths returned 404. The Twin Run was torn down. See `reviewed-integration-production-2026-09-24.json`, `reviewed-integration-local.json` and `docs-row-metrics.json`.
- PR #1134 adds ordered Notion sort controls, timestamp rules and schema-based choice ordering. All 121 Notion tests and Chrome desktop/390px checks pass; every seeded page, block, permission, comment and schema remains unchanged. The review follow-up passes 98 focused tests for malformed input and source-ID aliases; counts overlap. All CI passed and the PR merged as `4b5739a3`; both production workflows and hosted desktop/390px sorting checks pass, with protected state unchanged and teardown confirmed. See `notion-sort-controls.json` and `notion-sorts-production-2026-09-24.json`.
- PR #1135 fixes the hosted attached-email failure: downloadable RFC822 files, nested attachment isolation, and exact source bytes through forward/draft round trips. All 217 Gmail tests pass, including CRLF, folded headers and signed MIME preservation. Chrome desktop/390px validation passes for the initial fix. CI passed and the PR merged as `ee10e4f7`; both production workflows passed. Hosted Chrome desktop/390px forwarding and draft reopen pass; RFC822 and inline-image bytes remain exact, both source messages are unchanged, and teardown is confirmed. See `gmail-attached-message.json` and `gmail-attached-message-production-2026-09-24.json`.
- PR #1136 serves native Linear project/document/issue/comment/profile paths, including archived resources. All 116 Linear tests pass and local Chrome/native checks pass; current-head CI and hosted validation remain pending. See `linear-native-links.json`.
- PR #1137 fixes requested-reviewer responses and prevents read-only generic GitHub calls from creating state. All 350 GitHub tests pass; current-head CI/deployment and WKS-01/WKS-04 reruns remain pending. See `github-requested-reviewers.json`.
- PR #1138 adds typed nested AND/OR filters to Notion views. All 153 Notion tests and 23 final focused tests pass (overlapping); desktop and 390px Chrome editing preserve protected records. CI/deployment/hosted validation remain pending. See `notion-nested-filters.json`.
- PR #1124 adds Gmail compose windows and More options. Local Chrome/native checks and 188 Gmail regressions pass after rebasing on main; CI passes. Native print-dialog/afterprint inspection is blocked by host UI access; no print job was submitted. Production deployment and hosted checks are pending.

Remaining work includes provider settings/admin/billing/analytics/AI and collaboration surfaces, complete route/control inventories, provider-reference comparison, responsive and physical-mobile checks, and hosted verification of each new deployment. Examples include GitHub advanced review/Projects/Actions, Gmail provider inline composer layout and remaining compose controls, Calendar access requests/side apps, Docs headers/history/suggesting/table positioning, Sheets chart/filter/protection bindings and undo, Notion workspace/guest/advanced database views, Linear notifications/uploads/realtime, and Stripe reporting/admin. `readiness.json` deliberately keeps those gates pending.

## Package validation

The current evidence includes a clean locked install, 1,084 benchmark tests (26 skipped), candidate/relay/outcome regressions and 11 release-gate tests. Input scans contain no live credentials or image files. Historical invocation results are retained in `evidence/regression-log-summaries.json`; overlapping test counts are not additive. Later source changes must refresh hashes and relevant checks before release. No final ZIP has been created.

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
