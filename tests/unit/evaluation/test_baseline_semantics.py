from __future__ import annotations

import pytest

from arga_twins_benchmark.evaluation.baseline_semantics import enrich_baseline_semantics
from arga_twins_benchmark.evaluation.deterministic import CanonicalResource
from arga_twins_benchmark.evaluation.protocol import Mutation
from arga_twins_benchmark.specs.models import StateAssertionSpec


def _assertion(
    assertion_id: str,
    *,
    provider_role: str = "tracker",
    resource_type: str = "issue",
    selector: dict[str, object] | None = None,
    expected: dict[str, object] | None = None,
    cardinality: int = 1,
) -> StateAssertionSpec:
    return StateAssertionSpec.model_validate(
        {
            "id": assertion_id,
            "provider_role": provider_role,
            "resource_type": resource_type,
            "selector": selector or {"key": "OPS-1"},
            "expected": expected or {"equals_baseline": True},
            "cardinality": cardinality,
        }
    )


def _resource(
    resource_id: str,
    *,
    provider_role: str = "tracker",
    resource_type: str = "issue",
    **fields: object,
) -> CanonicalResource:
    return CanonicalResource(provider_role, resource_type, resource_id, dict(fields))


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
    assert "lacks baseline coverage" in result.unsupported[0].reason


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


def test_github_pull_artifact_exceptions_compare_the_rest_of_the_repository() -> None:
    assertion = _assertion(
        "repository-preserved",
        provider_role="code_host",
        resource_type="repository_snapshot",
        selector={"repository": "acme/web"},
        expected={
            "equals_baseline_except": [
                "pull_request.2.reviews",
                "pull_request.2.review_comments",
            ]
        },
    )
    old_snapshot = _resource(
        "acme/web",
        provider_role="code_host",
        resource_type="repository_snapshot",
        repository="acme/web",
        metadata={"visibility": "private"},
        reviews_by_pull={"2": []},
        review_comments_by_pull={"2": []},
    )
    new_snapshot = _resource(
        "acme/web",
        provider_role="code_host",
        resource_type="repository_snapshot",
        repository="acme/web",
        metadata={"visibility": "private"},
        reviews_by_pull={"2": [{"id": "10"}]},
        review_comments_by_pull={"2": [{"id": "11"}]},
    )
    pull = _resource(
        "acme/web#2",
        provider_role="code_host",
        resource_type="pull_request",
        repository="acme/web",
        number=2,
        title="Parser safety",
    )
    result = enrich_baseline_semantics(
        before=[old_snapshot, pull],
        after=[
            new_snapshot,
            pull,
            _resource(
                "acme/web#2:review:10",
                provider_role="code_host",
                resource_type="pull_request_review",
                repository="acme/web",
                pull_number=2,
                state="REQUEST_CHANGES",
            ),
            _resource(
                "acme/web#2:comment:11",
                provider_role="code_host",
                resource_type="pull_request_review_comment",
                repository="acme/web",
                pull_number=2,
                body="Replace eval",
            ),
        ],
        mutations=[],
        assertions=[assertion],
    )

    snapshot = next(resource for resource in result.resources if resource.resource_type == "repository_snapshot")
    assert snapshot.fields["equals_baseline_except"] == [
        "pull_request.2.reviews",
        "pull_request.2.review_comments",
    ]
    assert result.unsupported == ()

    collateral = CanonicalResource(
        new_snapshot.provider_role,
        new_snapshot.resource_type,
        new_snapshot.resource_id,
        {**new_snapshot.fields, "metadata": {"visibility": "public"}},
    )
    failed = enrich_baseline_semantics(
        before=[old_snapshot, pull],
        after=[collateral, pull],
        mutations=[],
        assertions=[assertion],
    )
    failed_snapshot = next(resource for resource in failed.resources if resource.resource_type == "repository_snapshot")
    assert "equals_baseline_except" not in failed_snapshot.fields
    assert failed.unsupported == ()


def test_gitlab_discussion_exception_is_scoped_to_one_merge_request() -> None:
    assertion = _assertion(
        "project-preserved",
        provider_role="code_host",
        resource_type="project_snapshot",
        selector={"project": "acme/web"},
        expected={"equals_baseline_except": ["merge_request.1.discussions"]},
    )
    before = [
        _resource(
            "acme/web",
            provider_role="code_host",
            resource_type="project_snapshot",
            project="acme/web",
            metadata={"visibility": "private"},
            discussions_by_merge_request={"1": [], "2": []},
        ),
        _resource(
            "acme/web!1",
            provider_role="code_host",
            resource_type="merge_request",
            project="acme/web",
            iid=1,
            title="Parser safety",
        ),
    ]
    after = [
        _resource(
            "acme/web",
            provider_role="code_host",
            resource_type="project_snapshot",
            project="acme/web",
            metadata={"visibility": "private"},
            discussions_by_merge_request={"1": [{"id": "d1"}], "2": []},
        ),
        before[1],
        _resource(
            "acme/web!1:discussion:d1:note:n1",
            provider_role="code_host",
            resource_type="merge_request_diff_discussion",
            project="acme/web",
            merge_request_iid=1,
            body="Replace eval",
        ),
    ]

    result = enrich_baseline_semantics(
        before=before,
        after=after,
        mutations=[],
        assertions=[assertion],
    )

    snapshot = next(resource for resource in result.resources if resource.resource_type == "project_snapshot")
    assert snapshot.fields["equals_baseline_except"] == ["merge_request.1.discussions"]
    assert result.unsupported == ()


def test_drive_permission_exception_uses_unique_title_and_marker_target() -> None:
    assertion = _assertion(
        "drive-preserved",
        provider_role="storage",
        resource_type="drive_snapshot",
        selector={"scope": "all_files"},
        expected={"equals_baseline_except": ["file[title=Evidence.txt,marker=SOC2-Q2-311].permissions"]},
    )
    target_before = _resource(
        "f1",
        provider_role="storage",
        resource_type="file",
        name="Evidence.txt",
        content_marker="SOC2-Q2-311",
        mime_type="text/plain",
        permissions=[],
    )
    target_after = _resource(
        "f1",
        provider_role="storage",
        resource_type="file",
        name="Evidence.txt",
        content_marker="SOC2-Q2-311",
        mime_type="text/plain",
        permissions=[{"id": "p1", "role": "reader"}],
    )
    other = _resource(
        "f2",
        provider_role="storage",
        resource_type="file",
        name="Other.txt",
        mime_type="text/plain",
        permissions=[],
    )
    before = [
        target_before,
        other,
        _resource(
            "all_files",
            provider_role="storage",
            resource_type="drive_snapshot",
            scope="all_files",
            stable_digest="old",
        ),
        _resource(
            "all_files",
            provider_role="storage",
            resource_type="file_collection",
            scope="all_files",
            stable_digest="old",
        ),
    ]
    after = [
        target_after,
        other,
        _resource(
            "f1:p1",
            provider_role="storage",
            resource_type="file_permission",
            file_id="f1",
            role="reader",
        ),
        _resource(
            "all_files",
            provider_role="storage",
            resource_type="drive_snapshot",
            scope="all_files",
            stable_digest="new",
        ),
        _resource(
            "all_files",
            provider_role="storage",
            resource_type="file_collection",
            scope="all_files",
            stable_digest="new",
        ),
    ]

    result = enrich_baseline_semantics(
        before=before,
        after=after,
        mutations=[],
        assertions=[assertion],
    )

    snapshot = next(resource for resource in result.resources if resource.resource_type == "drive_snapshot")
    assert snapshot.fields["equals_baseline_except"] == ["file[title=Evidence.txt,marker=SOC2-Q2-311].permissions"]
    assert result.unsupported == ()


def test_notion_lifecycle_exceptions_preserve_every_other_page_field() -> None:
    assertion = _assertion(
        "notion-preserved",
        provider_role="knowledge_base",
        resource_type="notion_snapshot",
        selector={"scope": "workspace"},
        expected={
            "equals_baseline_except": [
                "Policy Registry[Policy=POL-2].Lifecycle",
                "Policy Registry[Policy=POL-3].Lifecycle",
            ]
        },
    )

    def policy_resources(
        policy: str,
        lifecycle: str,
        *,
        digest: str,
    ) -> list[CanonicalResource]:
        resource_id = policy.lower()
        return [
            _resource(
                resource_id,
                provider_role="knowledge_base",
                resource_type="database_page",
                database="Policy Registry",
                Policy=policy,
                Lifecycle=lifecycle,
                Version="1",
                **{"properties.Lifecycle.select.name": lifecycle},
            ),
            _resource(
                resource_id,
                provider_role="knowledge_base",
                resource_type="page",
                title=policy,
                properties={"Policy": policy, "Lifecycle": lifecycle, "Version": "1"},
                archived=False,
            ),
            _resource(
                resource_id,
                provider_role="knowledge_base",
                resource_type="page_collection",
                title=policy,
                archived=False,
                stable_digest=digest,
            ),
        ]

    before = [
        *policy_resources("POL-2", "Current", digest="p2-old"),
        *policy_resources("POL-3", "Proposed", digest="p3-old"),
        _resource(
            "workspace",
            provider_role="knowledge_base",
            resource_type="notion_snapshot",
            scope="workspace",
            stable_digest="old",
        ),
    ]
    after = [
        *policy_resources("POL-2", "Superseded", digest="p2-new"),
        *policy_resources("POL-3", "Current", digest="p3-new"),
        _resource(
            "workspace",
            provider_role="knowledge_base",
            resource_type="notion_snapshot",
            scope="workspace",
            stable_digest="new",
        ),
    ]

    result = enrich_baseline_semantics(
        before=before,
        after=after,
        mutations=[],
        assertions=[assertion],
    )

    snapshot = next(resource for resource in result.resources if resource.resource_type == "notion_snapshot")
    assert snapshot.fields["equals_baseline_except"] == [
        "Policy Registry[Policy=POL-2].Lifecycle",
        "Policy Registry[Policy=POL-3].Lifecycle",
    ]
    assert result.unsupported == ()


def test_stripe_price_field_exceptions_keep_catalog_scope_bounded() -> None:
    assertion = _assertion(
        "stripe-preserved",
        provider_role="payments",
        resource_type="stripe_snapshot",
        selector={"scope": "catalog_and_customers"},
        expected={
            "equals_baseline_except": [
                "price[product_name=Pro Monthly,unit_amount=7900,currency=usd].nickname",
                "price[product_name=Pro Monthly,unit_amount=7900,currency=usd].lookup_key",
            ]
        },
    )
    target_before = _resource(
        "price-1",
        provider_role="payments",
        resource_type="price",
        product_name="Pro Monthly",
        unit_amount=7900,
        currency="usd",
        nickname=None,
        lookup_key=None,
        active=True,
    )
    target_after = _resource(
        "price-1",
        provider_role="payments",
        resource_type="price",
        product_name="Pro Monthly",
        unit_amount=7900,
        currency="usd",
        nickname="pro-monthly-usd-79",
        lookup_key="pro_monthly_usd_7900",
        active=True,
    )
    old_snapshot = _resource(
        "catalog_and_customers",
        provider_role="payments",
        resource_type="stripe_snapshot",
        scope="catalog_and_customers",
        customers_digest="customers",
        products_digest="products",
        prices_digest="old",
    )
    new_snapshot = _resource(
        "catalog_and_customers",
        provider_role="payments",
        resource_type="stripe_snapshot",
        scope="catalog_and_customers",
        customers_digest="customers",
        products_digest="products",
        prices_digest="new",
    )
    old_all_snapshot = _resource(
        "all",
        provider_role="payments",
        resource_type="stripe_snapshot",
        scope="all",
        customers_digest="customers",
        products_digest="products",
        prices_digest="old",
    )
    new_all_snapshot = _resource(
        "all",
        provider_role="payments",
        resource_type="stripe_snapshot",
        scope="all",
        customers_digest="customers",
        products_digest="products",
        prices_digest="new",
    )

    result = enrich_baseline_semantics(
        before=[target_before, old_snapshot, old_all_snapshot],
        after=[target_after, new_snapshot, new_all_snapshot],
        mutations=[],
        assertions=[assertion],
    )

    snapshot = next(
        resource
        for resource in result.resources
        if resource.resource_type == "stripe_snapshot" and resource.fields["scope"] == "catalog_and_customers"
    )
    assert snapshot.fields["equals_baseline_except"] == [
        "price[product_name=Pro Monthly,unit_amount=7900,currency=usd].nickname",
        "price[product_name=Pro Monthly,unit_amount=7900,currency=usd].lookup_key",
    ]
    assert result.unsupported == ()


def test_calendar_append_exception_resolves_one_new_sibling_target() -> None:
    preservation = _assertion(
        "calendar-preserved",
        provider_role="calendar",
        resource_type="calendar_snapshot",
        selector={"scope": "all"},
        expected={"equals_baseline_except": ["calendar[name=Work].events.append(CAL-330)"]},
    )
    target = _assertion(
        "target-event",
        provider_role="calendar",
        resource_type="event",
        selector={
            "calendar_name": "Work",
            "summary": "Budget review preparation",
            "start": "2030-05-16T13:45:00Z",
            "end": "2030-05-16T14:30:00Z",
        },
        expected={"present": True},
    )
    existing = _resource(
        "e1",
        provider_role="calendar",
        resource_type="event",
        calendar="Work",
        calendar_name="Work",
        summary="Existing",
        start="2030-05-16T12:00:00Z",
        end="2030-05-16T12:30:00Z",
    )
    created = _resource(
        "e2",
        provider_role="calendar",
        resource_type="event",
        calendar="Work",
        calendar_name="Work",
        summary="Budget review preparation",
        start="2030-05-16T13:45:00Z",
        end="2030-05-16T14:30:00Z",
    )
    before = [
        existing,
        _resource(
            "Work",
            provider_role="calendar",
            resource_type="event_collection",
            calendar="Work",
            event_count=1,
            deleted_count=0,
            stable_digest="old",
        ),
        _resource(
            "all",
            provider_role="calendar",
            resource_type="calendar_snapshot",
            scope="all",
            stable_digest="old",
        ),
        _resource(
            "all",
            provider_role="calendar",
            resource_type="provider_state",
            scope="all",
            stable_digest="old",
        ),
    ]
    after = [
        existing,
        created,
        _resource(
            "Work",
            provider_role="calendar",
            resource_type="event_collection",
            calendar="Work",
            event_count=2,
            deleted_count=0,
            stable_digest="new",
        ),
        _resource(
            "all",
            provider_role="calendar",
            resource_type="calendar_snapshot",
            scope="all",
            stable_digest="new",
        ),
        _resource(
            "all",
            provider_role="calendar",
            resource_type="provider_state",
            scope="all",
            stable_digest="new",
        ),
    ]

    result = enrich_baseline_semantics(
        before=before,
        after=after,
        mutations=[],
        assertions=[target, preservation],
    )

    snapshot = next(resource for resource in result.resources if resource.resource_type == "calendar_snapshot")
    assert snapshot.fields["equals_baseline_except"] == ["calendar[name=Work].events.append(CAL-330)"]
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


def test_exact_target_exclusion_counts_only_other_event_mutations() -> None:
    target_assertion = _assertion(
        "target",
        provider_role="calendar",
        resource_type="event",
        selector={
            "calendar": "Work",
            "summary": "Launch review",
            "start": "2030-05-16T15:00:00Z",
        },
        expected={"attendee_emails": ["alex@acme.example"]},
    )
    preservation = _assertion(
        "other-events",
        provider_role="calendar",
        resource_type="event_collection",
        selector={"exclude_exact_target": True},
        expected={"mutation_count": 0},
    )
    target_before = _resource(
        "target",
        provider_role="calendar",
        resource_type="event",
        calendar="Work",
        calendar_name="Work",
        summary="Launch review",
        start="2030-05-16T15:00:00Z",
        attendee_emails=[],
    )
    target_after = _resource(
        "target",
        provider_role="calendar",
        resource_type="event",
        calendar="Work",
        calendar_name="Work",
        summary="Launch review",
        start="2030-05-16T15:00:00Z",
        attendee_emails=["alex@acme.example"],
    )
    collection_before = _resource(
        "Work",
        provider_role="calendar",
        resource_type="event_collection",
        calendar="Work",
        stable_digest="old",
    )
    collection_after = _resource(
        "Work",
        provider_role="calendar",
        resource_type="event_collection",
        calendar="Work",
        stable_digest="new",
    )
    target_mutation = Mutation(
        "calendar",
        "event",
        "target",
        "update",
        before={"attendee_emails": []},
        after={"attendee_emails": ["alex@acme.example"]},
    )
    summary_mutation = Mutation(
        "calendar",
        "event_collection",
        "Work",
        "update",
        before={"stable_digest": "old"},
        after={"stable_digest": "new"},
    )

    result = enrich_baseline_semantics(
        before=[target_before, collection_before],
        after=[target_after, collection_after],
        mutations=[target_mutation, summary_mutation],
        assertions=[target_assertion, preservation],
    )

    collection = next(resource for resource in result.resources if resource.resource_type == "event_collection")
    assert collection.fields["exclude_exact_target"] is True
    assert collection.fields["mutation_count"] == 0
    assert result.unsupported == ()

    collateral = enrich_baseline_semantics(
        before=[target_before, collection_before],
        after=[target_after, collection_after],
        mutations=[
            target_mutation,
            Mutation("calendar", "event", "decoy", "update"),
        ],
        assertions=[target_assertion, preservation],
    )
    collateral_collection = next(
        resource for resource in collateral.resources if resource.resource_type == "event_collection"
    )
    assert collateral_collection.fields["mutation_count"] == 1


def test_duplicate_canonical_identity_is_rejected() -> None:
    duplicate = _resource("1", key="OPS-1")
    with pytest.raises(ValueError, match="duplicate canonical identity"):
        enrich_baseline_semantics(
            before=[duplicate, duplicate],
            after=[],
            mutations=[],
            assertions=[],
        )
