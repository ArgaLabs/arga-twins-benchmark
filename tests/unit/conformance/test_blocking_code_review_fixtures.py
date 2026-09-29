from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from arga_twins_benchmark.conformance.models import EvaluatorFixtureBundle
from arga_twins_benchmark.conformance.registry import (
    audit_conformance,
    catalog_verifier_bundles,
    execute_fixture_case,
    load_conformance_registry,
)

CATALOG_ROOT = Path("benchmark")
REGISTRY = CATALOG_ROOT / "conformance" / "registry.json"
FIXTURES = CATALOG_ROOT / "conformance" / "fixtures"
BUILDER = Path("scripts/build_blocking_code_review_fixtures.py")


def _load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_blocking_code_review_fixtures", BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_builder()
VARIANT_IDS = [variant.instance_id for variant in builder.VARIANTS]


@pytest.fixture(scope="module")
def audit() -> dict[str, object]:
    return audit_conformance(catalog_root=CATALOG_ROOT, registry_path=REGISTRY, fixture_root=FIXTURES)


def _result(audit: dict[str, object], case_id: str) -> dict[str, Any]:
    results = cast(list[dict[str, Any]], audit["evaluator_fixture_results"])
    return next(item for item in results if item["case_id"] == case_id)


def test_checked_in_fixtures_match_the_seed_derived_builder() -> None:
    assert builder.main(["--check"]) == 0


@pytest.mark.parametrize("instance_id", VARIANT_IDS)
def test_gold_and_equivalent_trajectory_pass_on_outcome(audit: dict[str, object], instance_id: str) -> None:
    gold = _result(audit, f"{instance_id}.gold")
    equivalent = _result(audit, f"{instance_id}.gold.semantic_equivalent")

    assert gold["passed"] and gold["task_success"] and gold["trace_policy_passed"]
    assert equivalent["passed"] and equivalent["task_success"]
    assert equivalent["trace_policy_passed"] is False
    assert equivalent["collateral_damage"] is False


@pytest.mark.parametrize("instance_id", VARIANT_IDS)
def test_negative_controls_fail_their_registered_predicates(audit: dict[str, object], instance_id: str) -> None:
    (entry,) = [item for item in load_conformance_registry(REGISTRY).entries if item.instance_id == instance_id]
    executable = [case for case in entry.negative_controls if case.fixture_id is not None]
    assert {case.case_id.rsplit(".", 1)[1] for case in executable} == {
        "missing_required_action",
        "collateral_mutation",
    }
    for case in executable:
        result = _result(audit, case.case_id)
        assertion_results = cast(dict[str, bool], result["assertion_results"])
        assert result["passed"] is True
        assert result["task_success"] is False
        assert result["collateral_damage"] is case.expected_collateral_damage
        assert case.intended_failure_assertion_ids
        assert all(assertion_results[item] is False for item in case.intended_failure_assertion_ids)


@pytest.mark.parametrize("instance_id", VARIANT_IDS)
def test_wrong_target_is_registered_pending_with_its_defect(instance_id: str) -> None:
    (entry,) = [item for item in load_conformance_registry(REGISTRY).entries if item.instance_id == instance_id]
    (wrong_target,) = [case for case in entry.negative_controls if case.case_id.endswith(".wrong_target")]
    assert wrong_target.fixture_id is None
    assert wrong_target.pending_reason is not None
    assert "invalid_grader" in wrong_target.pending_reason


@pytest.mark.xfail(
    strict=True,
    reason=(
        "A review on a non-target pull request is visible only through the trusted GitHub review "
        "total; grading raises a state-evidence error instead of failing the trial."
    ),
)
@pytest.mark.parametrize("variant", builder.VARIANTS, ids=VARIANT_IDS)
def test_wrong_target_review_fails_as_collateral_damage(variant: Any) -> None:
    fixture = EvaluatorFixtureBundle.model_validate(
        builder.build_fixture(variant, CATALOG_ROOT, include_ungradeable=True)
    )
    (case,) = [item for item in fixture.cases if item.case_id.endswith(".wrong_target")]
    bundle = catalog_verifier_bundles(CATALOG_ROOT)[variant.instance_id]

    result = execute_fixture_case(bundle=bundle, fixture_bundle=fixture, fixture_case=case)

    assert result.passed
