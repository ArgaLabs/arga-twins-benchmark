# Candidate-safe twin and documentation surface

## Purpose

Scored candidates need enough information to discover a provider API without receiving the twin implementation's own UI, schema, credentials, or control plane. The secure-default runner therefore gives the model exactly two tools:

- `provider_api` calls an ordinary relative data-plane path on one provisioned twin.
- `provider_docs` discovers and fetches the selected provider's actual official documentation.

The model receives the tool schemas and provider or role names. Twin base URLs, provider credentials, the Arga API key, admin URLs, seed state, reset controls, and grader artifacts remain inside the runner adapter. The checked-in documentation catalog is a navigation and security policy, not substitute documentation: its operation tags are internal search terms and are never returned to the model.

## Official documentation discovery

`provider_docs` has two read-only actions:

1. `search` returns matching catalog document IDs, titles, and official URLs.
2. `fetch` retrieves a returned document ID or an allowlisted link returned by an earlier fetch.

Fetches use HTTPS, exact provider-specific hosts, provider-specific path prefixes, bounded responses, and manual redirect validation. An input URL, redirect, or page link outside that provider's host and path scope is rejected. The tool cannot make arbitrary web requests, cannot cross from one Google product's docs to another, and has no twin or model-service credentials. Official OpenAPI or schema material is allowed when it is reached inside an official docs scope; only twin-hosted schema discovery is blocked.

The model receives at most 20,000 characters from one fetch, using query-centered excerpts when requested. The runner separately saves the exact bounded response body (up to 512 KiB), even when only a smaller excerpt was shown.

### Source registry

The catalog was reviewed on 2026-07-27. These are the official owners, versions, path scopes, and starting documents:

| Twin | Version label | Strict official scope | Starting document |
| --- | --- | --- | --- |
| Notion | 2026-03-11 | `developers.notion.com/llms.txt`, `/reference/` | `https://developers.notion.com/llms.txt` |
| Google Drive | v3 | `developers.google.com/workspace/drive/api/` | `https://developers.google.com/workspace/drive/api/reference/rest/v3` |
| Gmail | v1 | `developers.google.com/workspace/gmail/api/` | `https://developers.google.com/workspace/gmail/api/reference/rest` |
| Google Calendar | v3 | `developers.google.com/workspace/calendar/api/` | `https://developers.google.com/workspace/calendar/api/v3/reference` |
| GitHub | 2022-11-28 | `docs.github.com/en/rest` | `https://docs.github.com/en/rest?apiVersion=2022-11-28` |
| GitLab | v4 | `docs.gitlab.com/api/` | `https://docs.gitlab.com/api/rest/` |
| Discord | v10 | `docs.discord.com/developers/` | `https://docs.discord.com/developers/reference` |
| Jira | Cloud REST v3 | `developer.atlassian.com/cloud/jira/platform/rest/v3/` | `https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/` |
| Linear | rolling GraphQL schema | `linear.app/developers/` | `https://linear.app/developers/graphql?noRedirect=1` |
| Slack | rolling Web API | `docs.slack.dev/reference/methods`, `api.slack.com/methods/` | `https://docs.slack.dev/reference/methods` |
| Stripe | rolling API reference | `docs.stripe.com/api` | `https://docs.stripe.com/api` |

The complete navigation registry is [`official_api_docs.yaml`](../src/arga_twins_benchmark/providers/official_api_docs.yaml).

## Twin request policy

`provider_api` accepts only a provisioned provider or role and a relative path. It always rejects:

- absolute or network-path URLs, path traversal, host/auth/proxy overrides, and redirects;
- root discovery at `/` or `/api`;
- root-level UI and schema routes such as `/docs`, `/openapi.json`, `/swagger`, `/redoc`, `/schema`, their `/api/...` forms, and `/.well-known/openapi|schema`;
- root-level operational discovery such as `/health`, `/healthz`, `/ready`, `/readiness`, and `/metrics`, including `/api/...`;
- root-level control, admin, seed, reset, inspect, grading, grader, and `_twin` routes, including `/api/...`;
- GraphQL `__schema` and `__type(...)` introspection in JSON bodies or encoded query-string values.

Ordinary provider resources whose later path component or filename happens to be `admin`, `docs`, `schema`, `health`, or `openapi.json` remain valid. For example, GitHub repository content at `/repos/acme/app/contents/openapi.json` is not mistaken for twin schema discovery. Ordinary GraphQL `__typename` is also valid.

## Fairness, replay, and accounting

Official sites can change during a matrix. The suite uses a concurrency-safe first-fetch snapshot keyed by provider and exact URL. The first successful body is reused for all models, repeats, and resumed runs. Artifacts are:

- `official-docs-cache/manifest.json`: suite-scoped URL, owner, API version, response metadata, path, and SHA-256 provenance;
- `official-docs-cache/responses/<sha256>.body`: the exact bounded bytes used for replay;
- `trials/<trial>/official-docs-trace.json`: every search/fetch attempt, separate from provider activity;
- `trials/<trial>/official-docs-cache-ref.json`: the cache manifest reference and hashes used by that trial;
- `trials/<trial>/provider-trace.json`: business provider calls only.

Suite audit checks docs trace protocol and sequence, split call counts, and every successful fetch hash and byte length against the suite cache body. Semantic state grading, minimum business-call diagnostics, mutation analysis, and redundant-call detection continue to use only the provider trace.

Each instance retains its original `provider_api` call limit. Secure mode adds a separately enforced allowance of eight `provider_docs` calls and increases the model adapter's combined ceiling by the same amount, so normal documentation reads do not consume the business API budget.

## Running and feature gates

Secure local routing and official docs are on by default:

```bash
uv run arga-bench run-instance <instance-id> --model <model-id>
uv run arga-bench run-matrix development_pilot_48_v1
```

`--legacy-candidate-surface` disables the local candidate-safe restrictions and `provider_docs`. It is intended only for historical comparison, is recorded in the suite manifest, and cannot be changed when resuming a suite.

Arga's server-side candidate-safe profile is a separate deployment capability. Enable it only after the installed Arga CLI and target server support `twin-runs create --candidate-safe`:

```bash
uv run arga-bench run-matrix development_pilot_48_v1 \
  --arga-candidate-safe-profile
```

The flag is deliberately opt-in until that external deployment is confirmed. Local gateway enforcement remains active without it.

## Tradeoffs

Live official retrieval keeps the benchmark honest about API discovery and avoids committing third-party documentation copies. The suite snapshot makes comparisons within and across resumed runs reproducible, but a newly created suite may see a newer official page. Reports should retain the catalog review date, API version label, retrieval timestamp, ETag or Last-Modified when supplied, and content SHA-256. A future fully offline leaderboard can pre-populate the same snapshot format from reviewed official mirrors without changing the candidate tool contract.
