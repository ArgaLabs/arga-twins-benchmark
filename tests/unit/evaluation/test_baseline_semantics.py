from __future__ import annotations

import pytest

from arga_twins_benchmark.evaluation.baseline_semantics import enrich_baseline_semantics
from arga_twins_benchmark.evaluation.deterministic import CanonicalResource
from arga_twins_benchmark.evaluation.protocol import Mutation
from arga_twins_benchmark.specs.models import StateAssertionSpec


def _assertion(
    assertion_id: str,
    *,
    resource_type: str = "issue",
    selector: dict[str, object] | None = None,
    expected: dict[str, object] | None = None,
    cardinality: int = 1,
) -> StateAssertionSpec:
    return StateAssertionSpec.model_validate(
        {
            "id": assertion_id,
            "provider_role": "tracker",
            "resource_type": resource_type,
            "selector": selector or {"key": "OPS-1"},
            "expected": expected or {"equals_baseline": True},
            "cardinality": cardinality,
        }
    )


def _resource(
    resource_id: str,
    *,
    resource_type: str = "issue",
    **fields: object,
) -> CanonicalResource:
    return CanonicalResource("tracker", resource_type, resource_id, dict(fields))


def test_identity_baseline_facts_are_derived_without_guessing_provider_fields() -> None:
    before = [_resource("1", key="OPS-1", status="open")]
    after = [
        _resource("1", key="OPS-1", status="open"),
        _resource("2", key="OPS-2", status="open"),
    ]
    assertions = [
        _assertion("existing", expected={"new_since_baseline": False, "equals_baseline": True}),
        _assertion(
            "created",
            selector={"key": "OPS-2"},
            expected={"new_since_baseline": True, "equals_baseline": False},
        ),
    ]

    result = enrich_baseline_semantics(
        before=before,
        after=after,
        mutations=[],
        assertions=assertions,
    )

    by_id = {resource.resource_id: resource.fields for resource in result.resources}
    assert by_id["1"]["new_since_baseline"] is False
    assert by_id["1"]["equals_baseline"] is True
    assert by_id["2"]["new_since_baseline"] is True
    assert by_id["2"]["equals_baseline"] is False
    assert result.unsupported == ()


def test_fields_equal_baseline_honors_only_explicit_top_level_exceptions() -> None:
    assertion = _assertion(
        "preserved",
        expected={"fields_equal_baseline": True, "except": ["comments"]},
    )
    result = enrich_baseline_semantics(
        before=[_resource("1", key="OPS-1", status="open", comments=["old"])],
        after=[_resource("1", key="OPS-1", status="open", comments=["old", "new"])],
        mutations=[],
        assertions=[assertion],
    )

    assert result.resources[0].fields["fields_equal_baseline"] is True
    assert result.resources[0].fields["except"] == ["comments"]
    assert result.unsupported == ()


def test_unexcepted_field_change_is_not_marked_equal() -> None:
    assertion = _assertion(
        "not-preserved",
        expected={"fields_equal_baseline": True, "except": ["comments"]},
    )
    result = enrich_baseline_semantics(
        before=[_resource("1", key="OPS-1", status="open", comments=[])],
        after=[_resource("1", key="OPS-1", status="closed", comments=["new"])],
        mutations=[],
        assertions=[assertion],
    )

    assert result.resources[0].fields["fields_equal_baseline"] is False


def test_structured_cross_resource_exception_is_reported_and_left_absent() -> None:
    assertion = _assertion(
        "snapshot",
        resource_type="repository_snapshot",
        selector={"repository": "acme/api"},
        expected={"equals_baseline_except": ["pull_request.2.reviews"]},
    )
    result = enrich_baseline_semantics(
        before=[
            _resource(
                "acme/api",
                resource_type="repository_snapshot",
                repository="acme/api",
                digest="old",
            )
        ],
        after=[
            _resource(
                "acme/api",
                resource_type="repository_snapshot",
                repository="acme/api",
                digest="new",
            )
        ],
        mutations=[],
        assertions=[assertion],
    )

    assert "equals_baseline_except" not in result.resources[0].fields
    assert len(result.unsupported) == 1
    assert result.unsupported[0].construct == "equals_baseline_except"
    assert "not provable" in result.unsupported[0].reason


def test_simple_equals_baseline_except_is_proved_from_same_resource() -> None:
    assertion = _assertion(
        "simple-except",
        expected={"equals_baseline_except": ["comments"]},
    )
    result = enrich_baseline_semantics(
        before=[_resource("1", key="OPS-1", status="open", comments=[])],
        after=[_resource("1", key="OPS-1", status="open", comments=["new"])],
        mutations=[],
        assertions=[assertion],
    )

    assert result.resources[0].fields["equals_baseline_except"] == ["comments"]
    assert result.unsupported == ()


def test_mutation_count_uses_concrete_selected_resource_scope() -> None:
    assertion = _assertion("unchanged", expected={"mutation_count": 0})
    result = enrich_baseline_semantics(
        before=[
            _resource("1", key="OPS-1", status="open"),
            _resource("2", key="OPS-2", status="open"),
        ],
        after=[
            _resource("1", key="OPS-1", status="open"),
            _resource("2", key="OPS-2", status="closed"),
        ],
        mutations=[
            Mutation(
                twin="tracker",
                resource_type="issue",
                resource_id="2",
                operation="update",
                before={"status": "open"},
                after={"status": "closed"},
            )
        ],
        assertions=[assertion],
    )

    by_id = {resource.resource_id: resource.fields for resource in result.resources}
    assert by_id["1"]["mutation_count"] == 0
    assert "mutation_count" not in by_id["2"]


def test_whole_provider_mutation_scope_counts_all_role_mutations() -> None:
    assertion = _assertion(
        "provider-noop",
        resource_type="provider_state",
        selector={"scope": "all"},
        expected={"mutation_count": 0},
    )
    state = _resource("all", resource_type="provider_state", scope="all", digest="changed")
    result = enrich_baseline_semantics(
        before=[state],
        after=[state],
        mutations=[
            Mutation("tracker", "issue", "1", "update"),
            Mutation("other", "issue", "2", "update"),
        ],
        assertions=[assertion],
    )

    assert result.resources[0].fields["mutation_count"] == 1


def test_collection_delta_and_count_use_selector_cardinality() -> None:
    assertion = _assertion(
        "collection",
        resource_type="issue_collection",
        selector={"team_key": "OPS", "marker": "INC-1"},
        expected={"delta_from_baseline": 1, "count": 2},
        cardinality=2,
    )
    before = [
        _resource(
            "1",
            resource_type="issue_collection",
            team_key="OPS",
            marker="INC-1",
        )
    ]
    after = [
        *before,
        _resource(
            "2",
            resource_type="issue_collection",
            team_key="OPS",
            marker="INC-1",
        ),
    ]

    result = enrich_baseline_semantics(
        before=before,
        after=after,
        mutations=[],
        assertions=[assertion],
    )

    assert all(resource.fields["delta_from_baseline"] == 1 for resource in result.resources)
    assert all(resource.fields["count"] == 2 for resource in result.resources)


def test_generic_selector_operators_are_deterministic() -> None:
    assertion = _assertion(
        "operators",
        selector={
            "description_contains": "marker",
            "body_pattern": "^done [0-9]+$",
            "thread_id_in": ["t1", "t2"],
            "exclude_number": 3,
            "number_lte": 2,
            "baseline_only": True,
        },
        expected={"equals_baseline": True},
    )
    resource = _resource(
        "1",
        description="contains marker here",
        body="done 42",
        thread_id="t1",
        number=2,
    )
    result = enrich_baseline_semantics(
        before=[resource],
        after=[resource],
        mutations=[],
        assertions=[assertion],
    )

    assert result.resources[0].fields["equals_baseline"] is True


def test_ambiguous_exclusion_selector_fails_closed() -> None:
    assertion = _assertion(
        "ambiguous",
        resource_type="event_collection",
        selector={"exclude_exact_target": True},
        expected={"mutation_count": 0},
    )
    resource = _resource("work", resource_type="event_collection", calendar="Work")
    result = enrich_baseline_semantics(
        before=[resource],
        after=[resource],
        mutations=[],
        assertions=[assertion],
    )

    assert "mutation_count" not in result.resources[0].fields
    assert result.unsupported[0].construct == "mutation_count"


def test_duplicate_canonical_identity_is_rejected() -> None:
    duplicate = _resource("1", key="OPS-1")
    with pytest.raises(ValueError, match="duplicate canonical identity"):
        enrich_baseline_semantics(
            before=[duplicate, duplicate],
            after=[],
            mutations=[],
            assertions=[],
        )
