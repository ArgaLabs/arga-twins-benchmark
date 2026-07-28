from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from arga_twins_benchmark.conformance.models import ConformanceRegistry
from arga_twins_benchmark.conformance.registry import (
    ConformanceError,
    _apply_json_patch,  # pyright: ignore[reportPrivateUsage]
    audit_conformance,
    load_conformance_registry,
    load_fixture_bundles,
    validate_fixture_coverage,
    validate_registry_coverage,
)

CATALOG_ROOT = Path("benchmark")
REGISTRY = CATALOG_ROOT / "conformance" / "registry.json"
FIXTURES = CATALOG_ROOT / "conformance" / "fixtures"


@pytest.fixture(scope="module")
def audit() -> dict[str, object]:
    return audit_conformance(
        catalog_root=CATALOG_ROOT,
        registry_path=REGISTRY,
        fixture_root=FIXTURES,
    )


def test_checked_in_registry_has_one_to_one_control_coverage(audit: dict[str, object]) -> None:
    counts = cast(dict[str, int], audit["counts"])

    assert counts["instances"] == 48
    assert counts["gold_controls"] == 48
    assert counts["negative_controls"] == 144
    assert counts["semantic_equivalent_cases"] == 48
    assert counts["registered_cases"] == 240


def test_representative_evaluator_fixtures_pass_and_pending_fails_closed(
    audit: dict[str, object],
) -> None:
    counts = cast(dict[str, int], audit["counts"])
    results = cast(list[dict[str, Any]], audit["evaluator_fixture_results"])
    by_case = {cast(str, item["case_id"]): item for item in results}

    assert counts["executable_evaluator_cases"] == 5
    assert counts["passed_evaluator_cases"] == 5
    assert counts["failed_evaluator_cases"] == 0
    assert counts["pending_evaluator_cases"] == 235
    assert all(item["passed"] is True for item in results)
    equivalent = by_case["stripe_price_normalization_v1.clean.gold.semantic_equivalent"]
    assert equivalent["task_success"] is True
    assert equivalent["trace_policy_passed"] is False
    assert audit["leaderboard_ready"] is False
    assert "235 evaluator conformance cases are pending" in cast(list[str], audit["blockers"])


@pytest.mark.parametrize(
    ("case_id", "failed_assertions"),
    [
        (
            "stripe_price_normalization_v1.clean.create_replacement",
            {"state_catalog_preserved", "mutation_target_price", "mutation_policy.default_deny"},
        ),
        (
            "stripe_price_normalization_v1.clean.wrong_price",
            {
                "state_target_price",
                "state_catalog_preserved",
                "mutation_target_price",
                "mutation_policy.default_deny",
            },
        ),
        (
            "stripe_price_normalization_v1.clean.collateral_mutation",
            {"state_catalog_preserved", "mutation_policy.default_deny"},
        ),
    ],
)
def test_representative_negative_controls_fail_their_intended_predicates(
    audit: dict[str, object],
    case_id: str,
    failed_assertions: set[str],
) -> None:
    results = cast(list[dict[str, Any]], audit["evaluator_fixture_results"])
    result = next(item for item in results if item["case_id"] == case_id)
    assertion_results = cast(dict[str, bool], result["assertion_results"])

    assert result["task_success"] is False
    assert result["collateral_damage"] is True
    assert all(assertion_results[assertion_id] is False for assertion_id in failed_assertions)


def test_registry_rejects_missing_and_stale_entries() -> None:
    registry = load_conformance_registry(REGISTRY)
    missing = registry.model_copy(update={"entries": registry.entries[1:]})
    with pytest.raises(ConformanceError, match="coverage mismatch"):
        validate_registry_coverage(catalog_root=CATALOG_ROOT, registry=missing)

    first = registry.entries[0].model_copy(update={"verifier_sha256": "0" * 64})
    stale = registry.model_copy(update={"entries": [first, *registry.entries[1:]]})
    with pytest.raises(ConformanceError, match="verifier_sha256 is stale"):
        validate_registry_coverage(catalog_root=CATALOG_ROOT, registry=stale)


def test_registry_schema_rejects_duplicate_case_ids() -> None:
    raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    entries = cast(list[dict[str, Any]], raw["entries"])
    duplicate = copy.deepcopy(entries[0])
    entries.append(duplicate)

    with pytest.raises(ValueError, match="instance IDs must be unique"):
        ConformanceRegistry.model_validate(raw)


def test_fixture_coverage_rejects_missing_duplicate_and_orphan_cases() -> None:
    registry = load_conformance_registry(REGISTRY)
    fixtures = load_fixture_bundles(FIXTURES)
    assert len(fixtures) == 1

    with pytest.raises(ConformanceError, match="has no evaluator fixture"):
        validate_fixture_coverage(registry=registry, fixtures=[])
    with pytest.raises(ConformanceError, match="duplicate evaluator fixture"):
        validate_fixture_coverage(registry=registry, fixtures=[fixtures[0], fixtures[0]])

    first_case = fixtures[0].cases[0].model_copy(update={"case_id": "orphan.case"})
    orphan_bundle = fixtures[0].model_copy(update={"cases": [first_case]})
    with pytest.raises(ConformanceError, match="not registered"):
        validate_fixture_coverage(registry=registry, fixtures=[orphan_bundle])


def test_fixture_patch_is_strict_and_does_not_silently_create_replace_targets() -> None:
    document: dict[str, Any] = {"items": [{"name": "before"}]}
    _apply_json_patch(document, "replace", "/items/0/name", "after")
    assert document == {"items": [{"name": "after"}]}

    with pytest.raises(ConformanceError, match="does not exist"):
        _apply_json_patch(document, "replace", "/items/0/missing", "value")
    with pytest.raises(ConformanceError, match="already exists"):
        _apply_json_patch(document, "add", "/items/0/name", "duplicate")
    with pytest.raises(ConformanceError, match="out of range"):
        _apply_json_patch(document, "remove", "/items/9", None)
    with pytest.raises(ConformanceError, match="invalid JSON pointer escape"):
        _apply_json_patch(document, "replace", "/items/0/name~0suffix~2", "value")
