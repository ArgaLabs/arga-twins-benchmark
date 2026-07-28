from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from arga_twins_benchmark.catalog import validate_catalog
from arga_twins_benchmark.evaluation.deterministic import (
    CanonicalResource,
    ToolCallRecord,
    evaluate_deterministic,
)
from arga_twins_benchmark.evaluation.protocol import JsonValue, Mutation
from arga_twins_benchmark.specs.models import InstanceSpec, VerificationSpec

_TRACKER_INSTANCE = "tracker_migration_v1_jira_linear_github_distractor_002"
_TARGET_DESCRIPTION = (
    "Migration marker: MIG-32\n"
    "Code reference: acme/gateway#4\n"
    "Add circuit breaker telemetry.\n\n"
    "Migration source: OPS-3"
)


def _tracker_fixture() -> tuple[InstanceSpec, VerificationSpec]:
    documents = validate_catalog(Path("benchmark"))
    instance_document = next(
        document
        for document in documents
        if isinstance(document.model, InstanceSpec) and document.model.instance_id == _TRACKER_INSTANCE
    )
    instance = cast(InstanceSpec, instance_document.model)
    verification_path = (instance_document.path.parent / instance.verification_file).resolve()
    verifier = next(
        document.model
        for document in documents
        if isinstance(document.model, VerificationSpec) and document.path.resolve() == verification_path
    )
    return instance, verifier


def _tracker_resources(target_identifier: str) -> list[CanonicalResource]:
    return [
        CanonicalResource(
            "target_tracker",
            "issue",
            f"linear-{target_identifier}",
            {
                "identifier": target_identifier,
                "description_contains": "Migration source: OPS-3",
                "team_key": "OPS",
                "project": "Reliability",
                "title": "Add circuit breaker telemetry",
                "priority": 2,
                "description": _TARGET_DESCRIPTION,
            },
        ),
        CanonicalResource(
            "source_tracker",
            "issue_comment",
            "jira-comment-1",
            {
                "issue_key": "OPS-3",
                "body": f"Migrated to {target_identifier}.",
                "body_pattern": r"^Migrated to [A-Z]+-[0-9]+\.$",
                "count": 1,
            },
        ),
        CanonicalResource(
            "target_tracker",
            "issue",
            "linear-decoy",
            {
                "identifier": "OPS-1",
                "description_contains": "Migration source: OPS-8",
                "mutation_count": 0,
            },
        ),
    ]


def _tracker_trace(operation: str) -> list[ToolCallRecord]:
    return [
        ToolCallRecord("source_tracker", "GET", "/rest/api/3/search?jql=project%3DOPS", 200, False),
        ToolCallRecord("source_tracker", "GET", "/rest/api/3/issue/OPS-3/comment", 200, False),
        ToolCallRecord(
            "code_host",
            "GET",
            "/repos/acme/gateway/contents/.arga/tracker-migration.md",
            200,
            False,
        ),
        ToolCallRecord("code_host", "GET", "/repos/acme/gateway/pulls/4", 200, False),
        ToolCallRecord("target_tracker", "POST", "/graphql", 200, False, operation="team"),
        ToolCallRecord("target_tracker", "POST", "/graphql", 200, False, operation="issues"),
        ToolCallRecord("target_tracker", "POST", "/graphql", 200, True, operation=operation),
        ToolCallRecord("source_tracker", "POST", "/rest/api/3/issue/OPS-3/comment", 201, True),
        ToolCallRecord("target_tracker", "POST", "/graphql", 200, False, operation="issue"),
    ]


@pytest.mark.parametrize(
    ("operation", "target_identifier", "outcome"),
    [
        ("create", "OPS-3", "created"),
        ("update", "OPS-2", "migration_completed"),
    ],
)
def test_tracker_distractor_accepts_safe_creation_or_exact_provenance_reconciliation(
    operation: str,
    target_identifier: str,
    outcome: str,
) -> None:
    instance, verifier = _tracker_fixture()
    target_after: dict[str, JsonValue] = {
        "description_contains": "Migration source: OPS-3",
        "team_key": "OPS",
        "project": "Reliability",
        "title": "Add circuit breaker telemetry",
        "description": _TARGET_DESCRIPTION,
        "priority": 2,
    }
    if operation == "create":
        target_mutation = Mutation(
            "target_tracker",
            "issue",
            f"linear-{target_identifier}",
            "create",
            after=target_after,
        )
        trace_operation = "issueCreate"
    else:
        target_mutation = Mutation(
            "target_tracker",
            "issue",
            f"linear-{target_identifier}",
            "update",
            before={
                **target_after,
                "project": "Platform",
                "title": "Gateway migration staging",
                "description": "Migration source: OPS-3",
            },
            after=target_after,
        )
        trace_operation = "issueUpdate"
    source_comment = Mutation(
        "source_tracker",
        "issue_comment",
        "jira-comment-1",
        "create",
        after={
            "issue_key": "OPS-3",
            "body": f"Migrated to {target_identifier}.",
        },
    )

    grade = evaluate_deterministic(
        verifier,
        complexity=instance.complexity,
        resources=_tracker_resources(target_identifier),
        mutations=[target_mutation, source_comment],
        trace=_tracker_trace(trace_operation),
        output={
            "outcome": outcome,
            "source_identifier": "OPS-3",
            "destination_provider": "linear",
            "destination_project": "Reliability",
            "destination_identifier": target_identifier,
        },
    )

    assert grade.task_success is True
    assert grade.collateral_damage is False
    assert grade.assertion_results["mutation_policy.default_deny"] is True
    assert grade.assertion_results["output.contract"] is True
    assert grade.assertion_results["target_reconcile"] is True
