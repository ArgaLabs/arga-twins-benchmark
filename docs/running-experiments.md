# Running experiments

## Candidate contract

The initial adapter calls:

```http
POST /invoke
Content-Type: application/json

{
  "input": "<task prompt>",
  "benchmark": {
    "protocol": "arga-bench/1",
    "trial_id": "opaque random identifier",
    "current_time": "2030-05-14T12:00:00Z",
    "principal": "benchmark-user",
    "tenant": "acme"
  }
}
```

The candidate may return JSON or text. The adapter normalizes it into a retained invocation result. The request never contains the seed, expected state, verifier, gold solution, or meaningful hidden instance ID.

## Arga lifecycle

The live runner will use:

1. `POST /scenarios` with exact `seed_config` and no prompt.
2. `POST /sandbox-runs` for the candidate and selected twins.
3. `GET /sandbox-runs/{id}` until deployment readiness.
4. Sandbox events/logs until `scenario_seeded` succeeds.
5. `GET /runs/{id}/twins/diagnostics` before and after invocation.
6. `GET /runs/{id}/logs` for failure evidence.
7. `DELETE /sandbox-runs/{id}` in a `finally` block.

Fresh sandboxes are required for scored repetitions. A twin-only reset loop may be used for faster development, provided candidate memory and caches are independently reset.

## Experiment discipline

- Use identical paired instances across agents.
- Pin candidate commit, model, configuration, budgets, task version, seed hash, verifier version, runner version, and twin images.
- Randomize episode order deterministically.
- Run infrastructure-invalid episodes again without counting them as agent failures.
- Use five independent repetitions for the main benchmark.
- Cluster statistical uncertainty by semantic template.

Planned commands:

```text
arga-bench catalog validate
arga-bench compile
arga-bench run <instance> --agent-repo ... --agent-ref ...
arga-bench resume <suite-run-id>
arga-bench grade <trial-id>
arga-bench report <suite-run-id>
arga-bench conformance
```

Only catalog validation is implemented during Milestone 0.
