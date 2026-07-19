from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, TypeAdapter

from arga_twins_benchmark.specs.models import (
    BindingSpec,
    CatalogModel,
    ExperimentSpec,
    InstanceSpec,
    TemplateSpec,
    VerificationSpec,
    WorldSpec,
)

CATALOG_KINDS = {"world", "binding", "template", "instance", "verification", "experiment"}
CATALOG_ADAPTER: TypeAdapter[CatalogModel] = TypeAdapter(CatalogModel)


@dataclass(frozen=True)
class CatalogDocument:
    path: Path
    model: CatalogModel
    fingerprint: str


def fingerprint_model(model: BaseModel) -> str:
    return _fingerprint_payload(model.model_dump(mode="json"))


def _fingerprint_payload(payload: object) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def load_document(path: Path) -> CatalogDocument:
    raw_value: object = yaml.safe_load(path.read_text())
    if not isinstance(raw_value, dict):
        raise ValueError(f"{path}: expected a YAML object")
    raw = cast(dict[str, Any], raw_value)
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in CATALOG_KINDS:
        raise ValueError(f"{path}: unsupported catalog kind {kind!r}")
    model = CATALOG_ADAPTER.validate_python(raw)
    return CatalogDocument(path=path, model=model, fingerprint=fingerprint_model(model))


def fingerprint_document(path: Path) -> str:
    return load_document(path).fingerprint


def fingerprint_instance_bundle(catalog_root: Path, instance_id: str) -> str:
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
    world = next(
        (
            document.model
            for document in documents
            if isinstance(document.model, WorldSpec) and document.model.world_id == instance.world_id
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
    verification_path = (instance_document.path.parent / instance.verification_file).resolve()
    verification = next(
        (
            document.model
            for document in documents
            if isinstance(document.model, VerificationSpec) and document.path.resolve() == verification_path
        ),
        None,
    )
    if template is None or world is None or binding is None or verification is None:
        raise AssertionError("validated catalog is missing a referenced instance artifact")

    seeds: dict[str, object] = {}
    for twin, relative_path in sorted(instance.seed_files.items()):
        seed_value: object = json.loads((instance_document.path.parent / relative_path).read_text())
        seeds[twin] = seed_value

    payload: dict[str, object] = {
        "instance": instance.model_dump(mode="json"),
        "template": template.model_dump(mode="json"),
        "world": world.model_dump(mode="json"),
        "binding": binding.model_dump(mode="json"),
        "prompt": (instance_document.path.parent / instance.prompt_file).read_text(),
        "seeds": seeds,
        "verification": verification.model_dump(mode="json"),
    }
    return _fingerprint_payload(payload)


def validate_catalog(path: Path) -> list[CatalogDocument]:
    candidates = [path] if path.is_file() else sorted([*path.rglob("*.yaml"), *path.rglob("*.yml")])
    documents = [load_document(candidate) for candidate in candidates]
    if path.is_dir():
        _validate_references(documents)
    elif documents and isinstance(documents[0].model, (InstanceSpec, ExperimentSpec)):
        raise ValueError("instances and experiments must be validated against a catalog directory")
    return documents


def _validate_references(documents: list[CatalogDocument]) -> None:
    worlds: dict[str, WorldSpec] = {}
    bindings: dict[str, BindingSpec] = {}
    templates: dict[str, TemplateSpec] = {}
    instances: dict[str, tuple[Path, InstanceSpec]] = {}
    verifications: set[Path] = set()
    verification_ids: dict[str, VerificationSpec] = {}
    experiments: list[tuple[Path, ExperimentSpec]] = []
    experiment_ids: dict[str, ExperimentSpec] = {}

    def register_unique[T](index: dict[str, T], identifier: str, model: T, path: Path) -> None:
        if identifier in index:
            raise ValueError(f"{path}: duplicate catalog identifier {identifier!r}")
        index[identifier] = model

    for document in documents:
        model = document.model
        if isinstance(model, WorldSpec):
            register_unique(worlds, model.world_id, model, document.path)
        elif isinstance(model, BindingSpec):
            register_unique(bindings, model.binding_id, model, document.path)
        elif isinstance(model, TemplateSpec):
            register_unique(templates, model.template_id, model, document.path)
        elif isinstance(model, InstanceSpec):
            if model.instance_id in instances:
                raise ValueError(f"{document.path}: duplicate catalog identifier {model.instance_id!r}")
            instances[model.instance_id] = (document.path, model)
        elif isinstance(model, VerificationSpec):
            register_unique(verification_ids, model.verifier_id, model, document.path)
            verifications.add(document.path.resolve())
        else:
            register_unique(experiment_ids, model.experiment_id, model, document.path)
            experiments.append((document.path, model))

    variant_group_splits: dict[str, set[str]] = {}
    for instance_path, instance in instances.values():
        template = templates.get(instance.template_id)
        if template is None:
            raise ValueError(f"{instance_path}: unknown template {instance.template_id!r}")
        world = worlds.get(instance.world_id)
        if world is None:
            raise ValueError(f"{instance_path}: unknown world {instance.world_id!r}")
        if instance.authorization.tenant not in world.tenants:
            raise ValueError(f"{instance_path}: authorization tenant is not declared by the world")
        world_principals = {identity.id for identity in world.identities}
        if instance.authorization.principal not in world_principals:
            raise ValueError(f"{instance_path}: authorization principal is not declared by the world")
        binding = bindings.get(instance.binding_id)
        if binding is None:
            raise ValueError(f"{instance_path}: unknown binding {instance.binding_id!r}")
        missing_roles = set(template.required_roles) - set(binding.roles)
        if missing_roles:
            raise ValueError(f"{instance_path}: binding is missing roles {sorted(missing_roles)}")
        if instance.variant not in template.variants:
            raise ValueError(f"{instance_path}: variant {instance.variant!r} is not declared by the template")

        expected_twins = {binding.roles[role] for role in template.required_roles}
        if set(instance.seed_files) != expected_twins:
            raise ValueError(
                f"{instance_path}: seed twins {sorted(instance.seed_files)} do not match "
                f"bound twins {sorted(expected_twins)}"
            )

        prompt_path = instance_path.parent / instance.prompt_file
        if not prompt_path.is_file() or not prompt_path.read_text().strip():
            raise ValueError(f"{instance_path}: missing or empty prompt file {prompt_path}")
        for twin, seed_file in instance.seed_files.items():
            seed_path = instance_path.parent / seed_file
            if not seed_path.is_file():
                raise ValueError(f"{instance_path}: missing seed for {twin}: {seed_path}")
            seed: object = json.loads(seed_path.read_text())
            if not isinstance(seed, dict):
                raise ValueError(f"{seed_path}: seed must be a JSON object")

        verification_path = (instance_path.parent / instance.verification_file).resolve()
        if verification_path not in verifications:
            raise ValueError(f"{instance_path}: verification file is missing from the catalog: {verification_path}")
        variant_group_splits.setdefault(instance.variant_group, set()).add(instance.split)

    leaked_groups = {group: splits for group, splits in variant_group_splits.items() if len(splits) > 1}
    if leaked_groups:
        raise ValueError(f"variant groups cross splits: {leaked_groups}")

    for experiment_path, experiment in experiments:
        missing_instances = set(experiment.instances) - set(instances)
        if missing_instances:
            raise ValueError(f"{experiment_path}: unknown experiment instances {sorted(missing_instances)}")
