from pathlib import Path

from arga_twins_benchmark.catalog import fingerprint_document, fingerprint_instance_bundle, validate_catalog


def test_repository_catalog_validates() -> None:
    documents = validate_catalog(Path("benchmark"))
    kinds = {document.model.kind for document in documents}
    assert kinds == {"binding", "experiment", "instance", "template", "verification", "world"}


def test_fingerprint_is_stable() -> None:
    path = Path("benchmark/worlds/saas_company_01/world.yaml")
    assert fingerprint_document(path) == fingerprint_document(path)


def test_episode_bundle_fingerprint_includes_referenced_artifacts(tmp_path: Path) -> None:
    import shutil

    catalog = tmp_path / "benchmark"
    shutil.copytree("benchmark", catalog)
    instance_id = "blocking_code_review_v1_github_clean_001"
    original = fingerprint_instance_bundle(catalog, instance_id)

    prompt = catalog / "instances/dev/blocking_code_review_v1_github_clean_001/prompt.txt"
    prompt.write_text(prompt.read_text() + "\nAdditional constraint.")

    assert fingerprint_instance_bundle(catalog, instance_id) != original
