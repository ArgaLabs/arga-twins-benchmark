# Roadmap

## Milestone 0 — Contracts and offline catalog

Deliverables:

- Pydantic models and committed JSON Schemas.
- Catalog validation and deterministic content fingerprints.
- Controlled-clock, authorization, mutation, budget, and failure contracts.
- Two fully specified static development instances and deliberately invalid fixture tests.
- Recorded architectural decisions for public/private metadata and the candidate/grader boundary.

Exit criterion: every catalog artifact validates and fingerprints reproducibly without live infrastructure.

## Milestone 1 — One trustworthy vertical slice

Deliverables:

- Arga control-plane client using explicit scenario seeds, sandbox runs, diagnostics, logs, and teardown.
- HTTP candidate adapter for `POST /invoke`.
- Durable episode state machine and immutable result artifacts.
- Canonical state diffing, required/forbidden assertions, and infrastructure-invalid classification.
- Gold solution and known-wrong controls for one inspectable task.
- Ten equivalent resets and repeated gold passes.

Start with `blocking_code_review_v1_github_clean_001`, then add `meeting_amendment_v1_gmail_calendar_clean_001`.

The initial seed shapes were reviewed against `validation-server@1aa60e0`; that records the expected provider contract but does not replace live conformance.

Exit criterion: `arga-bench run` executes and grades one episode end to end, always tears down, and reproduces the same relevant state from reset.

## Milestone 2 — Small scientific pilot

Build four semantic templates with four variants each:

1. Meeting amendment reconciliation — Gmail + Calendar.
2. Blocking code review — GitHub/GitLab.
3. Incident escalation — Slack + Linear / Discord + Jira.
4. Approval-gated refund — Stripe + Slack.

The refund family is blocked until the Stripe seeder and conformance suite can deterministically create charges, approval evidence, and refundable state. Do not paper over that gap with grader-only hidden mutations.

The 16 instances must include clean, distractor, idempotent or operational-hurdle, provider-transfer, and paired authorized/unauthorized cases. Add batch execution, paired seeds, fixed budgets, randomized ordering, resume support, and clustered reporting.

Exit criterion: two agents can be compared on the same 16-instance matrix with complete traces, hard safety failures, and infrastructure-invalid episodes excluded from agent metrics.

## Milestone 3 — Calibration release

Expand to the proposal's 12 templates × four variants = 48 instances only after Milestone 2 passes. Add shared identities, tenants, permission and approval graphs, controlled failure schedules, provider-pair coverage, and private split packaging.

## Milestone 4 — Main benchmark

Expand to 60 templates and 240 instances, then run provider-transfer, tool-catalog, scaffolding, interface, asynchronous-recovery, and multi-tenant experiments.

## Immediate implementation order

1. Finish schema generation and invalid-fixture tests.
2. Implement the offline compiler and episode hash.
3. Implement the runner against fake Arga and fake agent adapters.
4. Add the first live GitHub vertical slice.
5. Add provider canonicalizers one at a time behind conformance gates.
6. Complete validation-server data/control-plane isolation before scoring untrusted agents.
