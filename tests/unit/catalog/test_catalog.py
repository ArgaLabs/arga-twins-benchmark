from pathlib import Path

import yaml

from arga_twins_benchmark.catalog import fingerprint_document, fingerprint_instance_bundle, validate_catalog
from arga_twins_benchmark.specs.models import ExperimentSpec, InstanceSpec, TemplateSpec


def test_repository_catalog_validates() -> None:
    documents = validate_catalog(Path("benchmark"))
    kinds = {document.model.kind for document in documents}
    assert kinds == {"binding", "experiment", "instance", "template", "verification", "world"}


def test_fingerprint_is_stable() -> None:
    path = Path("benchmark/worlds/saas_company_01/world.yaml")
    assert fingerprint_document(path) == fingerprint_document(path)


def test_development_pilot_has_twelve_families_and_forty_eight_instances() -> None:
    documents = validate_catalog(Path("benchmark"))
    templates = [document.model for document in documents if isinstance(document.model, TemplateSpec)]
    instances = [document.model for document in documents if isinstance(document.model, InstanceSpec)]
    pilot = next(
        document.model
        for document in documents
        if isinstance(document.model, ExperimentSpec) and document.model.experiment_id == "development_pilot_48_v1"
    )

    assert len(templates) == 12
    assert len(instances) == 48
    assert len(pilot.instances) == len(set(pilot.instances)) == 48
    assert set(pilot.instances) == {instance.instance_id for instance in instances}
    for template in templates:
        family_instances = [instance for instance in instances if instance.template_id == template.template_id]
        assert len(family_instances) == 4


def test_episode_bundle_fingerprint_includes_referenced_artifacts(tmp_path: Path) -> None:
    import shutil

    catalog = tmp_path / "benchmark"
    shutil.copytree("benchmark", catalog)
    instance_id = "blocking_code_review_v1_github_clean_001"
    original = fingerprint_instance_bundle(catalog, instance_id)

    prompt = catalog / "instances/dev/blocking_code_review_v1_github_clean_001/prompt.txt"
    prompt.write_text(prompt.read_text() + "\nAdditional constraint.")

    assert fingerprint_instance_bundle(catalog, instance_id) != original


def test_episode_bundle_fingerprint_ignores_only_output_fact_severity(tmp_path: Path) -> None:
    import shutil

    catalog = tmp_path / "benchmark"
    shutil.copytree("benchmark", catalog)
    instance_id = "blocking_code_review_v1_github_clean_001"
    original = fingerprint_instance_bundle(catalog, instance_id)
    verification_path = catalog / "instances/dev/blocking_code_review_v1_github_clean_001/verification.yaml"
    payload = yaml.safe_load(verification_path.read_text())
    contract = payload["output_contract"]
    contract["diagnostic_facts"]["target_change"] = contract["required_facts"].pop("target_change")
    verification_path.write_text(yaml.safe_dump(payload, sort_keys=False))

    assert fingerprint_instance_bundle(catalog, instance_id) == original
