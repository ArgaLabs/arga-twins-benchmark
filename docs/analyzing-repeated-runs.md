# Analyzing repeated benchmark runs

`arga-bench analyze-suite` turns a completed outcome-first semantic grade and its preserved suite artifacts into two aggregate reports:

- machine-readable JSON for downstream analysis;
- Markdown suitable for the benchmark report.

The command is offline. It does not call Arga, a twin, a model provider, or an official documentation site.

## Run it

Grade the preserved suite first:

```bash
uv run arga-bench grade-suite \
  runs/<suite-run-id> \
  --root benchmark \
  --output runs/<suite-run-id>/semantic-grade.json \
  --fail-on-incomplete
```

Then analyze the grade and the exact suite it names:

```bash
uv run arga-bench analyze-suite \
  runs/<suite-run-id>/semantic-grade.json \
  --suite-dir runs/<suite-run-id> \
  --root benchmark \
  --json-output runs/<suite-run-id>/repeated-analysis.json \
  --markdown-output runs/<suite-run-id>/repeated-analysis.md \
  --bootstrap-seed 20260727 \
  --bootstrap-resamples 10000
```

When `--suite-dir` is omitted, it defaults to the semantic grade's parent directory. The two outputs default to `repeated-analysis.json` and `repeated-analysis.md` inside that suite.

The analyzer refuses to combine mismatched evidence. It verifies the semantic grade protocol and identity, the exact `suite.json` SHA-256 recorded by the grader, the full trial-plan identity set, model/instance/repeat metadata, safe trial paths, and every trace digest recorded in the grade.

## Outcome views

The report gives pass, fail, unsafe, infrastructure-invalid, and grader-invalid counts at these grains:

- model;
- task (`instance_id`);
- semantic family;
- variant;
- provider-role/provider binding.

Only valid semantic grades enter pass rates. Invalid infrastructure and grader trials remain visible as counts but are excluded from rates, variance, and uncertainty estimates. Provider-role denominators overlap: a four-provider task contributes its outcome to all four roles it exercises.

## Repeats and uncertainty

For every task/model cluster, the report preserves outcomes in repeat order and calculates:

- whether all declared repeats are present and valid;
- whether every repeat has the same exact outcome;
- whether outcomes are mixed;
- the task's repeat pass rate;
- the sample variance of the binary pass indicator.

The model summary reports complete, exact-consistent, mixed, and incomplete task clusters plus mean within-task pass-indicator variance. Suites with fewer than three repeats receive an explicit warning.

The 95% confidence intervals use a fixed-seed percentile bootstrap. The resampling unit is the task, and all valid repeats stay inside their sampled task cluster. This avoids treating repeated runs of one task as independent tasks. Pairwise model differences use the same task-cluster principle on the intersection of tasks gradeable for both models; the reported direction is always `model_a - model_b`. The JSON records the seed, resample count, common-task count, point difference, interval, and task-cluster wins/ties/losses.

## Tool and documentation behavior

Provider API and official-documentation activity remain separate:

- provider call volume, calls with an HTTP status, successful 2xx calls, non-2xx calls, missing statuses, trace errors, and non-2xx rate;
- official-doc searches, fetches, successful searches/fetches, errors, cache hits, and failed fetch statuses;
- the same call aggregates by model and provider;
- pass/fail/unsafe outcomes for trials that used official docs versus trials that did not.

Call aggregates cover every scheduled trial with a readable trace, including partial traces retained for an invalid trial; validity remains visible in the outcome tables. Documentation use is observational. A higher pass rate among trials that read docs does not by itself show that docs caused the improvement.

## Endpoint discovery and probing indicators

The analyzer classifies provider paths internally and emits only aggregate categories:

- provider-root attempts;
- twin-hosted UI/documentation attempts;
- OpenAPI/schema/Swagger discovery attempts;
- GraphQL schema-introspection signatures;
- health/readiness/metrics attempts;
- control-plane routes;
- grader routes;
- absolute, traversal, or otherwise external destinations.

It also counts attempted unknown tools, web-search-like tools, and grader/control-like tools from invocation metadata. These are trajectory indicators, not proof of malicious intent. Ordinary root/schema discovery is kept separate from possible grader/control-plane probing.

## Redundant calls

The report has two efficiency views:

1. all action-equivalent fingerprint groups repeated at least twice, including calls beyond the first and the maximum repetition count;
2. runaway groups with at least five equivalent calls, alongside the grader's own five-call flag and repeat-attempt count.

Action- and attempt-fingerprint coverage are reported explicitly. If an older trace lacks fingerprints, the analyzer marks coverage as partial instead of inventing equivalence from request paths.

## Redaction boundary

Both outputs are aggregate-only. They deliberately omit:

- request and response bodies;
- provider request paths and query strings;
- all non-official URLs;
- credentials and environment values;
- raw GraphQL;
- trace error messages;
- redundancy fingerprints.

Official-documentation activity is grouped by provisioned provider, action, status, and cache behavior. The report does not need to reproduce documentation URLs or fetched text.
