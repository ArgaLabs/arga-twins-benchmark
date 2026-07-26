from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from arga_twins_benchmark.catalog import validate_catalog
from arga_twins_benchmark.evaluation.canonicalizers import CANONICALIZERS
from arga_twins_benchmark.evaluation.deterministic import (
    CanonicalResource,
    ToolCallRecord,
    evaluate_deterministic,
)
from arga_twins_benchmark.evaluation.protocol import JsonValue, Mutation
from arga_twins_benchmark.evaluation.state_capture import (
    CapturedProviderState,
    CapturedQueryState,
    TrustedStateSnapshot,
)
from arga_twins_benchmark.evaluation.state_evidence import (
    STANDARD_CANONICALIZER_COVERAGE,
    CanonicalizerCoverage,
    StateEvidenceError,
    _materialize_relational_proofs,  # pyright: ignore[reportPrivateUsage]
    _prove_baseline_reference,  # pyright: ignore[reportPrivateUsage]
    _suppress_entity_projection_echoes,  # pyright: ignore[reportPrivateUsage]
    build_deterministic_state_evidence,
)
from arga_twins_benchmark.specs.models import (
    ComplexitySpec,
    StateAssertionSpec,
    VerificationSpec,
)


def verification(
    *,
    assertion_expected: dict[str, Any] | None = None,
    assertion_selector: dict[str, Any] | None = None,
) -> VerificationSpec:
    return VerificationSpec.model_validate(
        {
            "kind": "verification",
            "verifier_id": "state-evidence-test",
            "gold_solution_id": "state-evidence-test.gold",
            "negative_control_ids": ["state-evidence-test.negative"],
            "expected_state": ["One exact issue exists."],
            "forbidden_state_changes": ["Any other state change."],
            "critical_requirements": ["The exact issue exists."],
            "deterministic": {
                "snapshot_queries": [
                    {
                        "id": "issues",
                        "provider_role": "tracker",
                        "method": "GET",
                        "path": "/issues",
                        "canonicalizer": "test_issues",
                    }
                ],
                "state_assertions": [
                    {
                        "id": "state.issue",
                        "provider_role": "tracker",
                        "resource_type": "issue",
                        "selector": assertion_selector or {"marker": "INC-7"},
                        "expected": assertion_expected or {"new_since_baseline": True},
                        "cardinality": 1,
                    }
                ],
                "mutation_policy": {
                    "default": "deny",
                    "required": [
                        {
                            "id": "mutation.issue",
                            "provider_role": "tracker",
                            "resource_type": "issue",
                            "operation": "create",
                            "selector": {
                                "marker": "INC-7",
                                "description_contains": "Incident: INC-7",
                            },
                            "fields": ["title", "description"],
                            "min_count": 1,
                            "max_count": 1,
                        }
                    ],
                    "allowed": [],
                },
                "trace_policy": {
                    "min_tool_calls": 6,
                    "required_calls": [
                        {
                            "id": "trace.read",
                            "provider_role": "tracker",
                            "methods": ["GET"],
                            "path_pattern": "^/issues$",
                            "min_count": 1,
                        }
                    ],
                },
            },
        }
    )


def issue_canonicalizer(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    if not isinstance(capture.body, dict):
        raise StateEvidenceError("test issue response must be an object")
    body = cast(dict[str, Any], capture.body)
    items = body.get("issues")
    if not isinstance(items, list):
        raise StateEvidenceError("test issue response is incomplete")
    resources: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for raw in cast(list[object], items):
        if not isinstance(raw, dict):
            raise StateEvidenceError("test issue must be an object")
        issue = cast(dict[str, Any], raw)
        resource_id = str(issue["id"])
        fields = {key: value for key, value in issue.items() if key != "id"}
        resources.append(CanonicalResource(capture.provider_role, "issue", resource_id, fields))
        stable.append({"id": resource_id, **fields})
    resources.append(
        CanonicalResource(
            capture.provider_role,
            "issue_collection",
            "all",
            {"scope": "all", "issues": stable},
        )
    )
    return resources


def snapshot(
    issues: list[dict[str, JsonValue]],
    *,
    provider_state: dict[str, JsonValue] | None = None,
    status_code: int = 200,
    path: str = "/issues",
) -> TrustedStateSnapshot:
    return TrustedStateSnapshot(
        providers={
            "test_tracker": CapturedProviderState(
                "test_tracker",
                "tracker",
                provider_state if provider_state is not None else {"issues": cast(list[JsonValue], issues)},
            )
        },
        queries={
            "issues": CapturedQueryState(
                "issues",
                "test_tracker",
                "tracker",
                "GET",
                path,
                "test_issues",
                status_code,
                {"issues": cast(list[JsonValue], issues)},
            )
        },
    )


def build(
    baseline: TrustedStateSnapshot,
    final: TrustedStateSnapshot,
    *,
    verifier: VerificationSpec | None = None,
):
    return build_deterministic_state_evidence(
        baseline=baseline,
        final=final,
        verification=verifier or verification(),
        canonicalizers={"test_issues": issue_canonicalizer},
        coverage={"test_issues": CanonicalizerCoverage(frozenset({"issue", "issue_collection"}))},
    )


def complexity() -> ComplexitySpec:
    return ComplexitySpec.model_validate(
        {
            "minimum_agent_steps": 6,
            "minimum_tool_calls": 6,
            "agent_steps": [
                {
                    "id": f"step-{index}",
                    "kind": "retrieve",
                    "description": f"Evidence step {index}.",
                    "depends_on": [] if index == 1 else [f"step-{index - 1}"],
                    "tool_interactions": ["trace.read" if index == 1 else f"trace.extra-{index}"],
                }
                for index in range(1, 7)
            ],
            "tool_interactions": [
                {
                    "id": "trace.read" if index == 1 else f"trace.extra-{index}",
                    "provider_role": "tracker",
                    "kind": "read",
                    "target": "issues",
                    "purpose": f"Read evidence {index}.",
                }
                for index in range(1, 7)
            ],
        }
    )


def test_builds_rule_projected_mutation_and_baseline_fact() -> None:
    baseline = snapshot([])
    final = snapshot(
        [
            {
                "id": "issue-generated-1",
                "marker": "INC-7",
                "title": "Checkout incident",
                "description": "Incident: INC-7\nCause: upstream typo",
            }
        ]
    )

    evidence = build(baseline, final)

    assert len(evidence.mutations) == 1
    mutation = evidence.mutations[0]
    assert mutation.resource_id == "issue-generated-1"
    assert mutation.after == {
        "description": "Incident: INC-7\nCause: upstream typo",
        "description_contains": "Incident: INC-7",
        "marker": "INC-7",
        "title": "Checkout incident",
    }
    issue = next(resource for resource in evidence.resources if resource.resource_type == "issue")
    assert issue.fields["new_since_baseline"] is True
    assert evidence.canonical_delta_count == 2
    assert evidence.projection_delta_count == 1
    grade = evaluate_deterministic(
        verification(),
        complexity=complexity(),
        resources=list(evidence.resources),
        mutations=list(evidence.mutations),
        trace=[ToolCallRecord("tracker", "GET", "/issues", 200, False) for _ in range(6)],
        output=None,
    )
    assert grade.task_success is True


def test_create_keeps_unapproved_fields_visible_to_default_deny() -> None:
    final = snapshot(
        [
            {
                "id": "issue-generated-1",
                "marker": "INC-7",
                "title": "Checkout incident",
                "description": "Incident: INC-7",
                "notify_external": True,
            }
        ]
    )

    evidence = build(snapshot([]), final)

    assert len(evidence.mutations) == 2
    governed, residual = evidence.mutations
    assert isinstance(governed.after, dict)
    assert "notify_external" not in governed.after
    assert cast(dict[str, JsonValue], residual.after)["notify_external"] is True
    grade = evaluate_deterministic(
        verification(),
        complexity=complexity(),
        resources=list(evidence.resources),
        mutations=list(evidence.mutations),
        trace=[ToolCallRecord("tracker", "GET", "/issues", 200, False) for _ in range(6)],
        output=None,
    )
    assert grade.assertion_results["mutation.issue"] is True
    assert grade.assertion_results["mutation_policy.default_deny"] is False
    assert grade.task_success is False


def test_create_ignores_only_declared_canonical_context() -> None:
    final = snapshot(
        [
            {
                "id": "issue-generated-1",
                "identifier": "OPS-1",
                "marker": "INC-7",
                "title": "Checkout incident",
                "description": "Incident: INC-7",
            }
        ]
    )

    evidence = build(snapshot([]), final)

    assert len(evidence.mutations) == 1
    assert isinstance(evidence.mutations[0].after, dict)
    assert evidence.mutations[0].after["identifier"] == "OPS-1"
    grade = evaluate_deterministic(
        verification(),
        complexity=complexity(),
        resources=list(evidence.resources),
        mutations=list(evidence.mutations),
        trace=[ToolCallRecord("tracker", "GET", "/issues", 200, False) for _ in range(6)],
        output=None,
    )
    assert grade.assertion_results["mutation.issue"] is True
    assert grade.assertion_results["mutation_policy.default_deny"] is True
    assert grade.task_success is True


def test_delete_projects_provider_context_but_retains_unapproved_fields() -> None:
    verifier = verification()
    required_rule = verifier.deterministic.mutation_policy.required[0].model_copy(
        update={"operation": "delete"},
    )
    mutation_policy = verifier.deterministic.mutation_policy.model_copy(
        update={"required": [required_rule]},
    )
    deterministic = verifier.deterministic.model_copy(
        update={"mutation_policy": mutation_policy},
    )
    verifier = verifier.model_copy(update={"deterministic": deterministic})
    baseline = snapshot(
        [
            {
                "id": "issue-generated-1",
                "identifier": "OPS-1",
                "marker": "INC-7",
                "title": "Checkout incident",
                "description": "Incident: INC-7",
                "notify_external": True,
            }
        ]
    )

    evidence = build(baseline, snapshot([]), verifier=verifier)

    assert len(evidence.mutations) == 2
    governed, residual = evidence.mutations
    assert isinstance(governed.before, dict)
    assert governed.before["identifier"] == "OPS-1"
    assert "notify_external" not in governed.before
    assert cast(dict[str, JsonValue], residual.before)["notify_external"] is True
    grade = evaluate_deterministic(
        verifier,
        complexity=complexity(),
        resources=list(evidence.resources),
        mutations=list(evidence.mutations),
        trace=[ToolCallRecord("tracker", "GET", "/issues", 200, False) for _ in range(6)],
        output=None,
    )
    assert grade.assertion_results["mutation.issue"] is True
    assert grade.assertion_results["mutation_policy.default_deny"] is False


def test_unlisted_entity_change_is_preserved_for_default_deny() -> None:
    baseline = snapshot([{"id": "old", "marker": "OLD", "title": "Old", "description": "untouched"}])
    final = snapshot(
        [
            {"id": "old", "marker": "OLD", "title": "Changed", "description": "untouched"},
            {
                "id": "new",
                "marker": "INC-7",
                "title": "Checkout incident",
                "description": "Incident: INC-7",
            },
        ]
    )

    evidence = build(baseline, final)

    assert [(mutation.resource_id, mutation.operation) for mutation in evidence.mutations] == [
        ("new", "create"),
        ("old", "update"),
    ]
    unexpected = evidence.mutations[1]
    assert isinstance(unexpected.after, dict)
    assert unexpected.after["title"] == "Changed"


def test_exact_query_contract_and_success_status_are_required() -> None:
    with pytest.raises(StateEvidenceError, match="contract differs"):
        build(snapshot([]), snapshot([], path="/different"))

    with pytest.raises(StateEvidenceError, match="HTTP status 206"):
        build(snapshot([]), snapshot([], status_code=206))


def test_raw_admin_change_without_canonical_change_fails_closed() -> None:
    baseline = snapshot([], provider_state={"issues": [], "hidden_counter": 0})
    final = snapshot([], provider_state={"issues": [], "hidden_counter": 1})

    with pytest.raises(StateEvidenceError, match="lacks one unambiguous"):
        build(baseline, final)


def test_unrelated_admin_delta_cannot_borrow_another_resource_representation() -> None:
    issue: dict[str, JsonValue] = {
        "id": "issue-generated-1",
        "marker": "INC-7",
        "title": "Checkout incident",
        "description": "Incident: INC-7",
    }
    baseline = snapshot([], provider_state={"issues": [], "hidden_counter": 0})
    final = snapshot(
        [issue],
        provider_state={
            "issues": cast(list[JsonValue], [issue]),
            "hidden_counter": 1,
        },
    )

    with pytest.raises(StateEvidenceError, match="hidden_counter"):
        build(baseline, final)


def test_admin_only_field_on_created_object_fails_closed() -> None:
    visible: dict[str, JsonValue] = {
        "id": "issue-generated-1",
        "marker": "INC-7",
        "title": "Checkout incident",
        "description": "Incident: INC-7",
    }
    raw = {**visible, "notify_external": True}

    with pytest.raises(StateEvidenceError, match="lacks one unambiguous"):
        build(
            snapshot([]),
            snapshot(
                [visible],
                provider_state={
                    "issues": cast(list[JsonValue], [raw]),
                },
            ),
        )


def test_hidden_resource_with_same_id_cannot_borrow_canonical_identity() -> None:
    issue: dict[str, JsonValue] = {
        "id": "issue-generated-1",
        "marker": "INC-7",
        "title": "Checkout incident",
        "description": "Incident: INC-7",
    }
    baseline = snapshot(
        [],
        provider_state={"issues": [], "hidden": []},
    )
    final = snapshot(
        [issue],
        provider_state={
            "issues": cast(list[JsonValue], [issue]),
            "hidden": cast(
                list[JsonValue],
                [{"id": "issue-generated-1", "title": "Checkout incident"}],
            ),
        },
    )

    with pytest.raises(StateEvidenceError, match="/hidden"):
        build(baseline, final)


def test_conflicting_matching_mutation_projections_fail_closed() -> None:
    verifier = verification()
    required = verifier.deterministic.mutation_policy.required[0]
    verifier.deterministic.mutation_policy.required.append(
        required.model_copy(
            update={
                "id": "mutation.issue-broader",
                "fields": ["title"],
            }
        )
    )
    final = snapshot(
        [
            {
                "id": "issue-generated-1",
                "marker": "INC-7",
                "title": "Checkout incident",
                "description": "Incident: INC-7",
            }
        ]
    )

    with pytest.raises(StateEvidenceError, match="conflicting projection contracts"):
        build(snapshot([]), final, verifier=verifier)


def test_required_projection_precedes_broader_allowed_rule() -> None:
    verifier = verification()
    required = verifier.deterministic.mutation_policy.required[0]
    verifier.deterministic.mutation_policy.allowed.append(
        required.model_copy(
            update={
                "id": "mutation.issue-broader",
                "selector": {"marker": "INC-7"},
                "fields": ["title", "description", "priority"],
                "min_count": 0,
            }
        )
    )
    final = snapshot(
        [
            {
                "id": "issue-generated-1",
                "marker": "INC-7",
                "title": "Checkout incident",
                "description": "Incident: INC-7",
            }
        ]
    )

    evidence = build(snapshot([]), final, verifier=verifier)

    assert len(evidence.mutations) == 1


def test_symbolic_baseline_reference_rejects_missing_or_null_fields() -> None:
    baseline_pull = CanonicalResource(
        "code_host",
        "pull_request",
        "repo#2",
        {"repository": "repo", "number": 2},
    )
    target = CanonicalResource(
        "code_host",
        "pull_request_review",
        "review-1",
        {"repository": "repo"},
    )

    with pytest.raises(StateEvidenceError, match="requires non-empty string"):
        _prove_baseline_reference(
            target=target,
            field_name="commit_id",
            reference="baseline.pull[2].head.sha",
            before=[baseline_pull],
        )

    with pytest.raises(StateEvidenceError, match="requires non-empty string"):
        _prove_baseline_reference(
            target=CanonicalResource(
                "code_host",
                "pull_request_review",
                "review-1",
                {"repository": "repo", "commit_id": ""},
            ),
            field_name="commit_id",
            reference="baseline.pull[2].head.sha",
            before=[
                CanonicalResource(
                    "code_host",
                    "pull_request",
                    "repo#2",
                    {
                        "repository": "repo",
                        "number": 2,
                        "head_sha": "",
                    },
                )
            ],
        )


def relational_assertions(
    relation: str,
) -> tuple[list[StateAssertionSpec], CanonicalResource, list[CanonicalResource]]:
    if relation == "attached_to":
        reference = StateAssertionSpec.model_validate(
            {
                "id": "sa_reference",
                "provider_role": "code_host",
                "resource_type": "pull_request_review",
                "selector": {
                    "repository": "acme/service",
                    "state": "REQUEST_CHANGES",
                },
                "expected": {"present": True},
                "cardinality": 1,
            }
        )
        target = StateAssertionSpec.model_validate(
            {
                "id": "sa_target",
                "provider_role": "code_host",
                "resource_type": "pull_request_review_comment",
                "selector": {
                    "repository": "acme/service",
                    "path": "src/service.py",
                },
                "expected": {"attached_to": "sa_reference"},
                "cardinality": 1,
            }
        )
        target_resource = CanonicalResource(
            "code_host",
            "pull_request_review_comment",
            "acme/service#7:comment:80",
            {
                "repository": "acme/service",
                "path": "src/service.py",
                "attached_to": 71,
            },
        )
        references = [
            CanonicalResource(
                "code_host",
                "pull_request_review",
                f"acme/service#7:review:{review_id}",
                {
                    "repository": "acme/service",
                    "state": "REQUEST_CHANGES",
                },
            )
            for review_id in (71, 72)
        ]
        return [reference, target], target_resource, references

    if relation == "tracker_identifier_matches":
        reference = StateAssertionSpec.model_validate(
            {
                "id": "sa_reference",
                "provider_role": "issue_tracker",
                "resource_type": "issue",
                "selector": {"marker": "INC-7"},
                "expected": {"present": True},
                "cardinality": 1,
            }
        )
        target = StateAssertionSpec.model_validate(
            {
                "id": "sa_target",
                "provider_role": "messaging",
                "resource_type": "message",
                "selector": {"marker": "INC-7"},
                "expected": {
                    "tracker_identifier_matches": "sa_reference.identifier",
                },
                "cardinality": 1,
            }
        )
        target_resource = CanonicalResource(
            "messaging",
            "message",
            "message-1",
            {
                "marker": "INC-7",
                "text": "Created OPS-1.",
            },
        )
        references = [
            CanonicalResource(
                "issue_tracker",
                "issue",
                f"issue-{index}",
                {
                    "identifier": f"OPS-{index}",
                    "marker": "INC-7",
                },
            )
            for index in (1, 2)
        ]
        return [reference, target], target_resource, references

    raise AssertionError(f"unsupported test relation: {relation}")


@pytest.mark.parametrize("relation", ["attached_to", "tracker_identifier_matches"])
def test_missing_relational_reference_becomes_a_normal_assertion_failure(
    relation: str,
) -> None:
    assertions, target, _ = relational_assertions(relation)

    resources = _materialize_relational_proofs(
        before=[],
        after=[target],
        assertions=assertions,
    )
    verifier = verification()
    deterministic = verifier.deterministic.model_copy(
        update={"state_assertions": assertions},
    )
    verifier = verifier.model_copy(update={"deterministic": deterministic})
    grade = evaluate_deterministic(
        verifier,
        complexity=complexity(),
        resources=list(resources),
        mutations=[],
        trace=[],
        output=None,
    )

    assert grade.assertion_results["sa_reference"] is False
    assert grade.assertion_results["sa_target"] is False


@pytest.mark.parametrize("relation", ["attached_to", "tracker_identifier_matches"])
def test_ambiguous_relational_reference_remains_a_grader_error(
    relation: str,
) -> None:
    assertions, target, references = relational_assertions(relation)

    with pytest.raises(StateEvidenceError, match="uniquely"):
        _materialize_relational_proofs(
            before=[],
            after=[target, *references],
            assertions=assertions,
        )


def test_page_projection_echo_requires_exact_nested_delta() -> None:
    page = Mutation(
        twin="knowledge_base",
        resource_type="page",
        resource_id="page-1",
        operation="update",
        before={
            "markdown": "old",
            "properties": {"Lifecycle": "Current", "Owner": "Alice"},
        },
        after={
            "markdown": "new",
            "properties": {"Lifecycle": "Current", "Owner": "Mallory"},
        },
    )
    markdown = Mutation(
        twin="knowledge_base",
        resource_type="page_markdown",
        resource_id="page-1",
        operation="update",
        before={"title": "Policy", "markdown": "old"},
        after={"title": "Policy", "markdown": "new"},
    )

    kept, suppressed = _suppress_entity_projection_echoes(
        [page, markdown],
        governed_keys={("knowledge_base", "page_markdown", "page-1")},
    )

    assert kept == [page, markdown]
    assert suppressed == 0


def test_missing_declared_resource_coverage_fails_even_for_empty_collection() -> None:
    with pytest.raises(StateEvidenceError, match="tracker/issue"):
        build_deterministic_state_evidence(
            baseline=snapshot([]),
            final=snapshot([]),
            verification=verification(),
            canonicalizers={"test_issues": issue_canonicalizer},
            coverage={"test_issues": CanonicalizerCoverage(frozenset({"issue_collection"}))},
        )


def test_unsupported_structured_baseline_exception_fails_closed() -> None:
    verifier = verification(assertion_expected={"equals_baseline_except": ["issue[marker=INC-7].description"]})
    baseline_issue: dict[str, JsonValue] = {
        "id": "issue-1",
        "marker": "INC-7",
        "title": "Checkout incident",
        "description": "Incident: INC-7",
    }

    with pytest.raises(StateEvidenceError, match="unsupported baseline state semantics"):
        build(
            snapshot([baseline_issue]),
            snapshot([baseline_issue]),
            verifier=verifier,
        )


def test_selector_pattern_is_materialized_as_a_proved_fact() -> None:
    verifier = verification(
        assertion_selector={
            "marker": "INC-7",
            "description_pattern": "^Incident: INC-7$",
        },
        assertion_expected={"present": True},
    )
    issue: dict[str, JsonValue] = {
        "id": "issue-1",
        "marker": "INC-7",
        "title": "Checkout incident",
        "description": "Incident: INC-7",
    }

    evidence = build(snapshot([]), snapshot([issue]), verifier=verifier)

    resource = next(item for item in evidence.resources if item.resource_type == "issue")
    assert resource.fields["description_pattern"] == "^Incident: INC-7$"
    assert resource.fields["present"] is True


def test_standard_coverage_matches_registry_and_covers_catalog_contracts() -> None:
    assert set(STANDARD_CANONICALIZER_COVERAGE) == set(CANONICALIZERS)

    documents = validate_catalog(Path("benchmark"))
    for document in documents:
        if not isinstance(document.model, VerificationSpec):
            continue
        covered: dict[str, set[str]] = {}
        for query in document.model.deterministic.snapshot_queries:
            covered.setdefault(query.provider_role, set()).update(
                STANDARD_CANONICALIZER_COVERAGE[query.canonicalizer].resource_types
            )
        required = {
            (assertion.provider_role, assertion.resource_type)
            for assertion in document.model.deterministic.state_assertions
        }
        required.update(
            (rule.provider_role, rule.resource_type)
            for rule in (
                *document.model.deterministic.mutation_policy.required,
                *document.model.deterministic.mutation_policy.allowed,
            )
        )
        assert not {
            (role, resource_type) for role, resource_type in required if resource_type not in covered.get(role, set())
        }, document.path
