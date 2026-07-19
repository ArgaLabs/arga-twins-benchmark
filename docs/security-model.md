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

Candidates receive only public provider `base_url` values, ordinary twin-native credentials/environment, optional provider MCP URLs, task prompt, and run metadata. They never receive the Arga API key, complete CLI response, `admin_url`, `proxy_token`, `seed_results`, fixture files, verifier configuration, gold solutions, snapshots, or lifecycle controls.

## Current blocker

Current public twin routing does not enforce a benchmark-specific data-path allowlist. A candidate that guesses administrative paths on its public twin host may be able to inspect or mutate hidden state. Until this is fixed, results are internal calibration rather than cheat-resistant public scores.

## Platform integrity gate

Before private tasks or public scoring:

1. Route candidate provider traffic through a deny-by-default data-plane gateway.
2. Keep Arga CLI authentication and complete CLI JSON in the control process only.
3. Deny `/admin/*`, `/_admin/*`, `/_twin/*`, `/inspect`, reset, log-clear, and scenario-export paths.
4. Expose authenticated, read-only diagnostics through `arga twin-runs diagnostics --json` for the grader.
5. Restrict candidate egress to twin and model gateways.
6. Retain server-side audit logs and run an integrity conformance probe.
7. Prefer ephemeral scoped credentials over raw provider or model secrets.
