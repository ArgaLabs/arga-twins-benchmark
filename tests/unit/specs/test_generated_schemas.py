import json
from pathlib import Path

from pydantic import BaseModel

from arga_twins_benchmark.specs import (
    BindingSpec,
    EpisodeResult,
    ExperimentSpec,
    InstanceSpec,
    TemplateSpec,
    VerificationSpec,
    WorldSpec,
)


def test_committed_schemas_match_models() -> None:
    schemas: dict[str, type[BaseModel]] = {
        "binding.schema.json": BindingSpec,
        "episode-result.schema.json": EpisodeResult,
        "experiment.schema.json": ExperimentSpec,
        "instance.schema.json": InstanceSpec,
        "template.schema.json": TemplateSpec,
        "verification.schema.json": VerificationSpec,
        "world.schema.json": WorldSpec,
    }
    for filename, model in schemas.items():
        committed: object = json.loads((Path("schemas") / filename).read_text())
        assert committed == model.model_json_schema()
