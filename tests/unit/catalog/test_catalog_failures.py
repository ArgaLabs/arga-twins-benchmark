from pathlib import Path

import pytest

from arga_twins_benchmark.catalog import validate_catalog


def test_instance_cannot_be_validated_without_catalog_context() -> None:
    instance = Path("benchmark/instances/dev/blocking_code_review_v1_github_clean_001/instance.yaml")
    with pytest.raises(ValueError, match="catalog directory"):
        validate_catalog(instance)


def test_catalog_rejects_duplicate_identifiers(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    content = """\
kind: binding
schema_version: "0.1"
binding_id: duplicate
roles: {code_host: github}
provider_contracts: {github: test-contract}
"""
    first.write_text(content)
    second.write_text(content)

    with pytest.raises(ValueError, match="duplicate catalog identifier"):
        validate_catalog(tmp_path)
