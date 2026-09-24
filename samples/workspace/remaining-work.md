# Remaining work after the Sheets validation PR

This is the handoff checklist after PR #1147. It is not a claim of eight-provider parity. The five tasks retain earlier passing hosted candidate episodes; later WKS-03 attempts include a business failure and a separately preserved browser-driver infrastructure timeout, while final WKS-04 exposed malformed Gmail send acceptance; both rollout gates remain pending; the remaining frontend, deployed-revision and package gates still prevent a final ZIP. Retain text/JSON evidence; do not restore screenshot files.

## Release the reviewed changes

- Confirm both production workflows for the final merged twin/API revisions. Start fresh Twin Runs and verify the served implementation, native outcomes, protected state and teardown. Add complete deployed-image/revision attestation; existing served source-block fingerprints do not attest every container.
- Hosted relative-date/Scenario-clock checks for PR #1146 now pass. PR #1147 hosted native outcomes pass for rule lifecycle, range dropdown refresh, checkbox mouse/keyboard actions, warning/rejection, relative formulas and mobile cancellation/reload. PR #1149 passed CI and both production deployments (`64c8c14b`); fresh hosted desktop queued successful/failed-write restoration and native state pass. Finish the 390px replay once the viewport override takes effect; keep the initial invalid marker before focus after reload as a separate open gap.
- Gmail native Print/afterprint now passes with two cancelled previews and unchanged native state. PR #1124 passed CI/review and merged as `94ab5965`; both production workflows passed. Finish hosted verification, including native Print after the Mac is unlocked. No print job was submitted.
- Fix Gmail malformed outbound MIME acceptance before message/draft mutation and implement native GitHub comment GET/PATCH/DELETE/list behavior exposed by final candidate runs; then complete CI, deployment and affected hosted revalidation.
- Reconcile and review the evidence PR #1145 and sample PR #14 against the final source revisions. Both remain drafts while full parity/release checks are open.

## Complete the eight frontend surfaces

| Twin | Work still required |
| --- | --- |
| GitHub | Complete advanced file/line review and review-management flows; remaining Projects field/view/grouping/automation controls; full Actions dispatch/log/run-management coverage; repository/organization settings, admin, analytics and AI/collaboration surfaces. Existing review lifecycle, requested-reviewer and artifact checks cover only selected flows. |
| Gmail | Finish inline composer/remaining menu fidelity; complete settings/delegation, account/admin, AI and collaboration coverage. Verify all contextual actions across inbox, conversation, draft, search, labels and mobile states. |
| Google Calendar | Finish access-request flows, side apps and reminder/notification delivery semantics; complete settings, sharing/admin, AI and collaborative flows. Verify cross-account permission changes and timezone/date behavior across the remaining views. |
| Google Docs | Headers/footers/footnotes, suggesting and review interactions, richer history comparisons, Drive picker/image upload, table positioning/pinning and complete menus. Finish concurrent-edit behavior and full settings/AI coverage while preserving native document structure and permissions. |
| Google Sheets | Saved/temporary filter views and color filtering; charts/pivots and protected ranges; undo/redo; noncontiguous selection, header/frozen-boundary dragging and autoscroll. Finish validation chip colors/multiple selection and comprehensive clipboard/structural-reference behavior. Complete collaborative editing, settings/AI and remaining grid/menu controls, including invalid-marker rendering before focus after reload. |
| Notion | Teamspaces, guests and full workspace/admin/permission flows; advanced database layouts and inline property editing, including relation/formula/rollup/files behavior. Finish AI and realtime collaboration while preserving the implemented page grants, groups and database filtering/sorting. |
| Linear | Remaining issue/project workflow and settings controls, inline collaboration/comments, project graphs, uploads and notifications, realtime behavior and AI surfaces. Extend the existing rich editor, links and project overview with the remaining provider flows. |
| Stripe | Complete reporting/analytics, billing/account/admin and AI surfaces; remaining fees, FX/conversion and transaction workflows. Every exposed action must update native state and preserve the correct currency/amount semantics. Existing invoice/refund and currency-ledger checks do not establish full dashboard parity. |

## Perform the complete fidelity audit

- Expand the generated source-candidate index (`scripts/inventory_twin_frontends.py`, `evidence/frontend-source-inventory.json`) into a live route-by-route, state-by-state inventory for all eight twins: buttons, menus, fields, dialogs, navigation, keyboard shortcuts and permission-dependent controls. Source occurrences are not audited or unique rendered controls.
- For each control, compare the real provider and twin, exercise its intended result and error/cancellation behavior, reload, and verify the native state. No placeholder success messages or disconnected local-only state count as completed features.
- Compare desktop and narrow layouts, typography, spacing, scrolling, overlays and empty/loading/error states. Existing bounded 390px checks do not constitute exhaustive pixel parity or physical mobile/touch validation.
- Real Notion and Linear references are at login screens; user-selected authenticated test workspaces are required for permission/admin comparisons. Stripe reporting and Assistant entry points were inspected read-only, but response and permission behavior remain unaudited.
- Verify shared API/browser state, multi-user permissions and revocation, concurrent changes, persistence, reset and run isolation across the remaining flows.

## Revalidate and package the sample

- Keep all five task prompts open to Computer Use, APIs or a mixture, with readily available GitHub/Gmail/Calendar/Sheets/Docs frontends and equivalent outcome grading.
- Preserve the five earlier passing hosted candidate reports and their original attempts/retry provenance, plus the correctly scored WKS-03 business failure, final WKS-04 unsafe artifact and same-state WKS-05 empty-index regrade. Preserve the separate WKS-03 browser-driver timeout as infrastructure invalid with its one completed exact-Scenario retry: business failure from unread approved Gmail terms, no unsafe side effects, teardown confirmed. Do not retry that ordinary completed failure solely to obtain a pass. Do not hide failures or retry ordinary completed failures solely to obtain a pass. Revalidate affected tasks on the final deployment; broaden browser-agent coverage where the prior successful candidate chose APIs.
- Run final regression and package-gate checks; refresh evidence hashes, source-tree hash, manifests and deployed revisions. Keep the eight frontend gates pending until their full audits actually pass.
- Only then permit the packager to create the operator-only ZIP, inspect its contents, and deliver the archive with run instructions. Candidate-facing handoffs must continue to exclude operator fixtures, grading contracts and credentials.
