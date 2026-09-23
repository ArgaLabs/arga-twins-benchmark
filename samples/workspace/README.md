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

Gmail label visibility and conversation/draft count changes also passed a fresh production Chrome check with native readback and confirmed teardown. See `evidence/gmail-production-2026-09-22.json`. A subsequent saved-Scenario production check verified native paging, a searchable archived message with an explicit empty label list, exactly 52 Inbox conversations and two distinct draft rows at 390px. Native state was unchanged and teardown confirmed; see `evidence/gmail-empty-labels-production-2026-09-22.json`. Select-all-matching is now deployed and passed the combined Workspace hosted check described below.

Docs document tab navigation/lifecycle, nesting, duplication, drag reordering, outlines, deep links and tab-specific edits/exports are implemented in twin PR #1100 and deployed through combined PR #1101, with 190 unique regressions and local Chrome desktop/390px checks. Named-range IDs now remain unique across tabs and duplicated subtrees, including primary-tab deletion. Hosted tab creation, nesting, duplication, isolated editing, duplicate deletion and mobile dialogs passed; see `evidence/docs-document-tabs.json` and `evidence/workspace-integration-production-2026-09-22.json`. These feature checks do not complete the Docs or eight-twin frontend audits.

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

Production Chrome checks verified Sheets range formatting, formula-preserving copy/cut, dependent reference updates, quoted multiline paste, keyboard navigation across a rendered-window boundary, and the 390px Edit menu. Native readback and teardown passed. These are manual feature checks; see `evidence/sheets-range-production-2026-09-22.json`. Full eight-twin parity and the final ZIP remain gated.

The deployed Sheets viewport also passed a fresh production Chrome check of a 5,000-row × 100-column grid, native sizes/hidden dimensions, wheel scrolling, final-cell navigation, pending edits and hidden-cell editing. Native readback and teardown passed; the manual API setup and limitations are recorded in `evidence/sheets-viewport-production-2026-09-22.json`.

Sheets frozen panes are deployed at revision `cce5f0d2` and passed a fresh saved-Scenario production Chrome check: distant navigation, native row/column freeze changes, pending edits, formula recalculation, mobile menus and unchanged protected state. Teardown is confirmed; see `evidence/sheets-frozen-production-2026-09-22.json`.

Sheets merge-cell controls and rendering are in PR #1101, with 196 regressions plus Chrome desktop/390px checks of warning cancellation, merge modes, unmerge, hidden/offscreen anchors and frozen rows. Border/frozen-pane integration is verified locally in PR #1099, and PR #1101 now combines merged outer borders with native perimeter reads and independent frozen dividers. Hosted merge/border/mobile checks passed; irregular merged-perimeter provider-reference comparison remains pending.

Twin PR #1101 combined Gmail bulk-selection, Docs tabs and Sheets border/merge changes. All 573 combined Docs/Sheets/Gmail/Workspace regressions passed, with clean generated JavaScript and Ruff checks. CI passed and revision `559f01ef` deployed successfully through both production workflows. Fresh saved-Scenario Chrome checks and native readback preserved protected records; teardown is confirmed. See `evidence/workspace-integration.json` and `evidence/workspace-integration-production-2026-09-22.json`. Superseded component PRs #1097, #1099 and #1100 are closed.

The combined Workspace PR also passed 70 focused regressions after rejecting malformed merge types with atomic rollback (six new cases). Linear table structure menus are in twin PR #1102: 100 regressions plus Chrome desktop/390px checks cover row/column editing, alignment, keyboard movement, undo/redo and native comment state. Mobile footer spacing keeps Comment clickable. Replay checks also exposed and fixed deterministic ID collisions: generated comments/issues/projects now preserve explicitly seeded or API-supplied records. CI passed and PR #1102 merged at `af3730d5`. Both production deployments succeeded. The hosted check preserved the seeded comment and protected issue, and table-comment submission passed, but pointer Save lost description edits because the table toolbar moved the button during focus changes. PR #1105 fixes this, with 43 tests and desktop/mobile Chrome persistence checks; CI passed and both production deployments succeeded at `1ea7872a`; fresh hosted validation is pending. See `evidence/linear-table-controls.json`, `evidence/linear-table-production-2026-09-22.json` and `evidence/linear-pointer-save.json`. The broader Linear audit remains open.

The combined hosted check found two follow-ups: folder navigation retained Gmail search results (fixed and deployed in PR #1103 at `166c8f2a`, with 22 tests and Chrome desktop/mobile checks; fresh hosted folder-navigation validation pending), and Sheets displayed old dependent formula values until reload (native recalculation was correct; frontend refresh fixed in PR #1104 with 92 tests and Chrome desktop/mobile/merged-cell checks; a review follow-up clears the stale refresh-failure status after a successful retry, with 12 focused tests; CI passed and it merged at `baed86f9`, with both production workflows running). The read-only GitHub reference audit still finds substantial Actions/Projects gaps; see `evidence/github-reference-audit-2026-09-22.json`. These findings keep the release gate closed.

GitHub Actions artifacts are implemented in draft twin PR #1106: archive bytes, signed redirects, expiry, deletion, native pagination and permissions, replayable Scenario fixtures, and a responsive artifact table. All 320 GitHub regressions pass. Local Chrome desktop/390px inspection and mobile deletion/reload passed, preserving the protected repository. Both candidate adapters returned the same verified archive bytes and kept admin routes blocked. Chrome itself blocked the download navigation, so that browser check and hosted deployment remain open. See `evidence/github-artifacts.json`; no screenshot files are retained.

The optional browser relay passed 133 candidate/runner/outcome regressions and a four-action local Chrome transport check. It requires an external Computer Use driver and does not itself establish hosted candidate coverage. See `evidence/browser-relay-2026-09-23.json`. A fresh hosted Gmail folder-navigation attempt timed out before provider endpoints were available; its teardown confirmation is still pending. This infrastructure attempt is preserved in `evidence/gmail-folder-production-attempt-2026-09-23.json`.

A recovery scheduling bug was reproduced with 105 older stalled cleanup records. PR #1107 keeps provisioning capacity separate and rotates through both queues, with 81 local regressions passing. Its immutable creation-time cursor also passed a regression for progress updates during failed retries. CI/deployment and resolution of the hosted stall remain pending; the scheduling bug has not been established as that stall’s cause. See `evidence/recovery-queue-fairness.json`. The draft GitHub artifact PR passed the manually dispatched full Twin Fidelity workflow after its public-metadata contract correction; root PR CI remains skipped while draft.
