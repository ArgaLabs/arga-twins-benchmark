from __future__ import annotations

from pathlib import Path

from arga_twins_benchmark.catalog import validate_catalog
from arga_twins_benchmark.evaluation.canonicalizers import (
    CANONICALIZERS,
    CODE_COLLAB_CANONICALIZERS,
    PRODUCTIVITY_CANONICALIZERS,
    canonicalizers,
)
from arga_twins_benchmark.specs.models import VerificationSpec


def test_combined_registry_contains_each_provider_canonicalizer_once() -> None:
    assert not (set(CODE_COLLAB_CANONICALIZERS) & set(PRODUCTIVITY_CANONICALIZERS))
    assert set(CANONICALIZERS) == (
        set(CODE_COLLAB_CANONICALIZERS) | set(PRODUCTIVITY_CANONICALIZERS)
    )
    assert len(CANONICALIZERS) == len(CODE_COLLAB_CANONICALIZERS) + len(
        PRODUCTIVITY_CANONICALIZERS
    )


def test_combined_registry_factory_returns_an_independent_copy() -> None:
    first = canonicalizers()
    first.pop(next(iter(first)))

    assert len(canonicalizers()) == len(CANONICALIZERS)


def test_combined_registry_covers_every_catalog_snapshot_query() -> None:
    documents = validate_catalog(Path("benchmark"))
    referenced = {
        query.canonicalizer
        for document in documents
        if isinstance(document.model, VerificationSpec)
        for query in document.model.deterministic.snapshot_queries
    }

    assert referenced == set(CANONICALIZERS)
