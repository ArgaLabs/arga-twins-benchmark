# ArgaBench

ArgaBench evaluates whether AI agents can complete realistic work across
multiple software services while producing the required outcome and avoiding
unsafe side effects. ArgaBench v1 contains 40 deterministic tasks across IT,
CRM, marketing, developer, and e-commerce workflows, executed against
Arga-hosted service twins and graded from trusted before-and-after state.

The task index is in
[`benchmark/argabench_40/TASKS.md`](benchmark/argabench_40/TASKS.md), and the
machine-readable suite is in
[`benchmark/argabench_40/suite.json`](benchmark/argabench_40/suite.json).

## Quick start

Install dependencies, validate the suite, and run the tests:

```bash
uv sync --group dev
uv run python scripts/build_argabench_40.py validate
uv run pytest -q
```

## Reproduce the results

Install and authenticate the Arga CLI, then provide an Arga API key and the
model-provider keys required by the 32-profile matrix:

```bash
arga login

export ARGA_API_URL=https://api.argalabs.com
export ARGA_API_KEY='<arga-key>'
export ANTHROPIC_API_KEY='<anthropic-key>'
export OPENAI_API_KEY='<openai-key>'
export GEMINI_API_KEY='<gemini-key>'
```

Stage and verify the exact benchmark Scenarios, preflight the model profiles,
and run three independent repeats. The full run schedules 3,840 trials and
incurs Arga and model-provider usage:

```bash
uv run python scripts/build_argabench_40.py stage
uv run python scripts/build_argabench_40.py seed-check
uv run python scripts/preflight_argabench_model_matrix.py \
  --output runs/argabench-model-preflight.json

uv run python scripts/run_argabench_model_repeats.py \
  --output runs/argabench-40-three-repeats \
  --repeat-start 1 \
  --repeat-count 3
```

Generate the three semantic reports, combine them, and audit their saved
evidence:

```bash
for repeat in 1 2 3; do
  uv run python scripts/report_argabench_semantic_matrix.py \
    "runs/argabench-40-three-repeats/repeat-${repeat}" \
    "reports/argabench-40-repeat-${repeat}"
done

uv run python scripts/report_argabench_repeats.py \
  --repeat 1=reports/argabench-40-repeat-1/semantic-report.json \
  --repeat 2=reports/argabench-40-repeat-2/semantic-report.json \
  --repeat 3=reports/argabench-40-repeat-3/semantic-report.json \
  --output reports/argabench-40-repeated

uv run python scripts/audit_argabench_trial_evidence.py \
  --repeat 1=reports/argabench-40-repeat-1/semantic-report.json \
  --repeat 2=reports/argabench-40-repeat-2/semantic-report.json \
  --repeat 3=reports/argabench-40-repeat-3/semantic-report.json \
  --output reports/argabench-40-trial-evidence-audit.json
```

Model outputs are stochastic, so reproduction means running the same tasks,
profiles, repeat protocol, graders, and evidence audit—not obtaining identical
text or scores. See [running experiments](docs/running-experiments.md) for
resume and retry procedures.
