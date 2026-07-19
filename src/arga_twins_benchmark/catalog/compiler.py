from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from arga_twins_benchmark.catalog.loader import fingerprint_instance_bundle, validate_catalog
from arga_twins_benchmark.specs.models import BindingSpec, InstanceSpec, TemplateSpec


def compile_scenario(catalog_root: Path, instance_id: str) -> dict[str, Any]:
    """Compile one benchmark instance into an exact Arga Scenario import payload.

    The task prompt is deliberately absent: Arga receives only deterministic
    twin fixture state. The candidate adapter receives the prompt later.
    """

    documents = validate_catalog(catalog_root)
    instance_document = next(
        (
            document
            for document in documents
            if isinstance(document.model, InstanceSpec) and document.model.instance_id == instance_id
        ),
        None,
    )
    if instance_document is None or not isinstance(instance_document.model, InstanceSpec):
        raise ValueError(f"unknown instance {instance_id!r}")
    instance = instance_document.model

    template = next(
        (
            document.model
            for document in documents
            if isinstance(document.model, TemplateSpec) and document.model.template_id == instance.template_id
        ),
        None,
    )
    binding = next(
        (
            document.model
            for document in documents
            if isinstance(document.model, BindingSpec) and document.model.binding_id == instance.binding_id
        ),
        None,
    )
    if template is None or binding is None:
        raise AssertionError("validated catalog is missing a referenced template or binding")

    seed_config: dict[str, Any] = {}
    for twin, relative_path in sorted(instance.seed_files.items()):
        seed_path = instance_document.path.parent / relative_path
        seed_config[twin] = json.loads(seed_path.read_text())

    twins = sorted({binding.roles[role] for role in template.required_roles})
    content_hash = fingerprint_instance_bundle(catalog_root, instance_id)
    return {
        "name": f"arga-bench/{instance_id}/{content_hash[:12]}",
        "description": f"Deterministic fixture for benchmark instance {instance_id}",
        "twins": twins,
        "seed_config": seed_config,
        "tags": ["arga-bench", f"instance:{instance_id}", f"content-sha256:{content_hash}"],
    }


def write_compiled_scenario(catalog_root: Path, instance_id: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(compile_scenario(catalog_root, instance_id), indent=2, sort_keys=True) + "\n")
