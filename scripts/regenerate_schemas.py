from __future__ import annotations

import json
from pathlib import Path

from arga_twins_benchmark.conformance.models import (
    ConformanceRegistry,
    EvaluatorFixtureBundle,
    LiveCaseEvidence,
    LiveLifecycleEvidence,
)
from arga_twins_benchmark.specs import (
    BindingSpec,
    EpisodeResult,
    ExperimentSpec,
    InstanceSpec,
    TemplateSpec,
    VerificationSpec,
    WorldSpec,
)

SCHEMAS = {
    "binding.schema.json": BindingSpec,
    "conformance-registry.schema.json": ConformanceRegistry,
    "episode-result.schema.json": EpisodeResult,
    "evaluator-conformance-fixture.schema.json": EvaluatorFixtureBundle,
    "experiment.schema.json": ExperimentSpec,
    "instance.schema.json": InstanceSpec,
    "live-case-conformance.schema.json": LiveCaseEvidence,
    "live-lifecycle-conformance.schema.json": LiveLifecycleEvidence,
    "template.schema.json": TemplateSpec,
    "verification.schema.json": VerificationSpec,
    "world.schema.json": WorldSpec,
}


def main() -> None:
    output_dir = Path("schemas")
    output_dir.mkdir(exist_ok=True)
    for filename, model in SCHEMAS.items():
        payload = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        (output_dir / filename).write_text(payload)


if __name__ == "__main__":
    main()
