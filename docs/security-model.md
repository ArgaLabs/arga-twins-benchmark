# Security model

## Required trust boundary

```text
Candidate zone
  agent + ordinary provider APIs + scoped model endpoint
                     |
                     | data plane only
                     v
               Twin data gateway

Control zone
  benchmark runner + authenticated Arga CLI
                     |
                     v
Private grader plane
  hidden seed + gold + snapshots + reset capability
```

The runner adapter receives sanitized twin connection details, but the model does not. The model receives only the task prompt, provisioned provider/role names, and schemas for `provider_api` and `provider_docs`. It never receives twin base URLs or credentials, the Arga API key, complete CLI response, `admin_url`, `proxy_token`, `seed_results`, fixture files, verifier configuration, gold solutions, snapshots, or lifecycle controls.

Human-facing benchmark CLI JSON is also recursively redacted before it reaches stdout. Credential-bearing fields, including credential-valued provider seed environment variables, are scrubbed without modifying the underlying response object. Exact lifecycle evidence remains available only in the mode-`0600` trusted artifacts used by the grader.

## Candidate gateway

The local gateway rejects absolute destinations, host/auth overrides, traversal, root UI/schema/health routes, GraphQL introspection, and root control/admin/seed/reset/inspect/grader surfaces. Official documentation is fetched independently through exact provider host/path allowlists and never through a twin host. Server-side `--candidate-safe` routing remains a separately deployed defense-in-depth profile and is feature-gated until the installed Arga CLI and server both support it.

## Platform integrity gate

Before private tasks or public scoring:

1. Route candidate provider traffic through the candidate-safe data-plane gateway and deploy the matching server-side profile.
2. Keep Arga CLI authentication and complete CLI JSON in the control process only.
3. Deny twin roots, UI/OpenAPI/schema/health surfaces, `/admin/*`, `/_admin/*`, `/_twin/*`, `/inspect`, reset, log-clear, and scenario-export paths.
4. Expose authenticated, read-only diagnostics through `arga twin-runs diagnostics --json` for the grader.
5. Restrict candidate egress to twin and model gateways.
6. Retain server-side audit logs and run an integrity conformance probe.
7. Prefer ephemeral scoped credentials over raw provider or model secrets.

The detailed route contract, official source registry, suite snapshot design, and replay artifacts are documented in [candidate-safe twin and documentation surface](candidate-safe-surface.md).
