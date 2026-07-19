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
  benchmark runner + Arga API credential
                     |
                     v
Private grader plane
  hidden seed + gold + snapshots + reset/admin access
```

Candidates must never receive the Arga API key, fixture files, verifier configuration, gold solutions, admin/reset credentials, or control-plane URLs.

## Current blocker

Current Arga sandboxes can place candidate containers on the same network as twins. A candidate may be able to discover direct twin URLs and call administrative, inspection, reset, log-clear, or export routes. Public preview routing also lacks a benchmark-specific data-path allowlist.

Until this is fixed, experiment results are internal calibration results, not cheat-resistant leaderboard scores.

## Platform integrity gate

Before private tasks or public scoring:

1. Separate candidate/data and grader/control networks.
2. Route candidate provider traffic through a deny-by-default gateway.
3. Deny `/admin/*`, `/_admin/*`, `/_twin/*`, `/inspect`, reset, log-clear, and scenario-export routes.
4. Give the grader a private capability-scoped credential.
5. Restrict candidate egress to twin and model gateways.
6. Retain server-side audit logs and run an integrity conformance probe.
7. Prefer ephemeral scoped credentials over raw provider or model secrets.
