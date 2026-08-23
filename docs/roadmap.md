# Status and roadmap

## Active release: ArgaBench v1

The active release is `argabench-40-v1`: 40 deterministic tasks across five
domains, with eight tasks per domain. Its suite, task prompts, Scenario payloads,
model matrix, and fairness audit live under
[`benchmark/argabench_40`](../benchmark/argabench_40/). The dedicated runners
support profile matrices, three independent repeats, bounded concurrency,
immutable attempt preservation, explicit retry provenance, trusted before/after
state capture, mediated provider traces, and offline semantic reporting.

Current maintenance priorities are:

1. Keep task contracts, Scenario seeds, verifier capture queries, and public
   evidence synchronized by content hash.
2. Re-run only trials made non-comparable by a corrected task, verifier, or
   capture contract; preserve the superseded attempt and retry provenance.
3. Keep fail/unsafe classification consistent: missing required work is
   `fail`, while an actual prohibited side effect is `unsafe`.
4. Continue publishing repeat-level variance and uncertainty from at least
   three independent repeats.

## Retained development pilot

The separate `development_pilot_48_v1` catalog contains 12 semantic families
and four variants per family. It remains useful for compiler, generic runner,
and evaluator development, but it is not ArgaBench v1 and is not a released
leaderboard set.

The checked-in conformance audit reports:

- `leaderboard_ready=false`;
- 5 of 240 evaluator cases passing and 235 pending; and
- no live case or reset/isolation records.

Promoting any of those 48 instances would require current-fingerprint gold,
semantic-equivalence, and negative-control evidence plus ten-reset,
fresh-provision isolation, mutation-visibility, and teardown evidence. Until
then, documentation and reports must call them development instances rather
than scored episodes.

## External platform gaps

The installed Arga CLI exposes Scenario import/list and twin-run
create/status/reset/teardown, but not `twin-runs diagnostics` or a stable
twin-specific logs command. Trusted provider readers therefore remain the
grading source for baseline and final state. A server-side candidate-safe
profile remains defense in depth; the local mediated gateway is still required
to keep twin addresses, credentials, and control-plane routes out of model
context.
