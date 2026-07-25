from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, cast

from arga_twins_benchmark.evaluation.deterministic import CanonicalResource
from arga_twins_benchmark.evaluation.protocol import Mutation
from arga_twins_benchmark.specs.models import StateAssertionSpec

_BASELINE_FACT_KEYS = frozenset(
    {
        "new_since_baseline",
        "equals_baseline",
        "equals_baseline_except",
        "fields_equal_baseline",
        "mutation_count",
        "delta_from_baseline",
        "count",
    }
)
_WHOLE_PROVIDER_SCOPES = frozenset({"all", "all_files", "mailbox", "workspace"})
_SIMPLE_FIELD_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_GITHUB_PULL_EXCEPTION = re.compile(r"^pull_request\.(?P<number>[1-9][0-9]*)\.(?P<kind>reviews|review_comments)$")
_GITLAB_DISCUSSION_EXCEPTION = re.compile(r"^merge_request\.(?P<number>[1-9][0-9]*)\.discussions$")
_DRIVE_PERMISSION_EXCEPTION = re.compile(r"^file\[title=(?P<title>[^,\]]+),marker=(?P<marker>[^\]]+)\]\.permissions$")
_NOTION_LIFECYCLE_EXCEPTION = re.compile(r"^(?P<database>[^\[]+)\[Policy=(?P<policy>[^\]]+)\]\.Lifecycle$")
_STRIPE_PRICE_EXCEPTION = re.compile(
    r"^price\[product_name=(?P<product_name>[^,\]]+),"
    r"unit_amount=(?P<unit_amount>[0-9]+),"
    r"currency=(?P<currency>[^,\]]+)\]\."
    r"(?P<field>nickname|lookup_key)$"
)
_CALENDAR_APPEND_EXCEPTION = re.compile(r"^calendar\[name=(?P<calendar>[^\]]+)\]\.events\.append\((?P<marker>[^)]+)\)$")


@dataclass(frozen=True)
class UnsupportedBaselineAssertion:
    assertion_id: str
    construct: str
    reason: str


@dataclass(frozen=True)
class BaselineEnrichmentResult:
    """Canonical resources plus only baseline facts proved by the supplied evidence."""

    resources: tuple[CanonicalResource, ...]
    unsupported: tuple[UnsupportedBaselineAssertion, ...] = field(default_factory=tuple)
    derived_facts: Mapping[str, Mapping[str, Any]] = field(default_factory=lambda: dict[str, Mapping[str, Any]]())


@dataclass(frozen=True)
class _SelectorResult:
    matches: bool
    unsupported_reason: str | None = None


@dataclass(frozen=True)
class _ResolvedExactTarget:
    target_key: tuple[str, str, str]
    selected_before: Mapping[tuple[str, str, str], CanonicalResource]
    selected_after: Mapping[tuple[str, str, str], CanonicalResource]


def enrich_baseline_semantics(
    *,
    before: Sequence[CanonicalResource],
    after: Sequence[CanonicalResource],
    mutations: Sequence[Mutation],
    assertions: Sequence[StateAssertionSpec],
) -> BaselineEnrichmentResult:
    """Enrich after-state resources with provider-neutral baseline facts.

    This layer deliberately does not guess provider semantics. Resources are
    paired only by their canonical identity, collection counts are based only
    on unambiguous selectors, and mutation counts use explicit canonical
    resource scopes. Unsupported constructs are returned to the caller and are
    left absent so the deterministic evaluator fails closed.
    """

    before_index = _resource_index(before, label="before")
    after_index = _resource_index(after, label="after")
    enriched_fields = {key: dict(resource.fields) for key, resource in after_index.items()}
    unsupported: list[UnsupportedBaselineAssertion] = []
    derived_facts: dict[str, dict[str, Any]] = {}

    for assertion in assertions:
        requested = _requested_baseline_facts(assertion)
        if not requested:
            continue

        exact_target: _ResolvedExactTarget | None = None
        if "exclude_exact_target" in assertion.selector:
            exact_target, selector_error = _resolve_exact_target_exclusion(
                assertion,
                assertions=assertions,
                before=before_index,
                after=after_index,
            )
            if exact_target is None:
                selected_before = {}
                selected_after = {}
            else:
                selected_before = dict(exact_target.selected_before)
                selected_after = dict(exact_target.selected_after)
        else:
            selected_before, before_error = _select_resources(assertion, before_index, before_index)
            selected_after, after_error = _select_resources(assertion, after_index, before_index)
            selector_error = before_error or after_error
        if selector_error is not None:
            unsupported.extend(
                UnsupportedBaselineAssertion(assertion.id, key, selector_error) for key in sorted(requested)
            )
            continue

        facts_by_key: dict[tuple[str, str, str], dict[str, Any]] = {key: {} for key in selected_after}
        assertion_facts: dict[str, Any] = {}

        for fact_key in sorted(requested):
            if fact_key == "new_since_baseline":
                for key in selected_after:
                    facts_by_key[key][fact_key] = key not in before_index
                continue

            if fact_key == "equals_baseline":
                for key in selected_after:
                    old = before_index.get(key)
                    facts_by_key[key][fact_key] = old is not None and old.fields == after_index[key].fields
                continue

            if fact_key == "fields_equal_baseline":
                except_fields, error = _simple_except_fields(assertion)
                if error is not None:
                    unsupported.append(UnsupportedBaselineAssertion(assertion.id, fact_key, error))
                    continue
                for key in selected_after:
                    old = before_index.get(key)
                    facts_by_key[key][fact_key] = old is not None and _without_fields(
                        old.fields, except_fields
                    ) == _without_fields(after_index[key].fields, except_fields)
                    if "except" in assertion.expected:
                        facts_by_key[key]["except"] = list(except_fields)
                continue

            if fact_key == "equals_baseline_except":
                except_fields, error = _simple_equals_baseline_except(assertion)
                expected_value = assertion.expected[fact_key]
                if error is None:
                    for key in selected_after:
                        old = before_index.get(key)
                        equal = old is not None and _without_fields(old.fields, except_fields) == _without_fields(
                            after_index[key].fields, except_fields
                        )
                        if equal:
                            facts_by_key[key][fact_key] = expected_value
                    continue

                equal, structured_error = _structured_equals_baseline_except(
                    assertion,
                    assertions=assertions,
                    before=before_index,
                    after=after_index,
                    selected_before=selected_before,
                    selected_after=selected_after,
                )
                if structured_error is not None:
                    unsupported.append(
                        UnsupportedBaselineAssertion(
                            assertion.id,
                            fact_key,
                            structured_error,
                        )
                    )
                    continue
                if equal:
                    for key in selected_after:
                        facts_by_key[key][fact_key] = expected_value
                continue

            if fact_key == "mutation_count":
                if exact_target is not None:
                    mutation_count = _mutation_count_excluding_exact_target(
                        assertion,
                        target_key=exact_target.target_key,
                        mutations=mutations,
                    )
                    error = None
                    for key in selected_after:
                        facts_by_key[key]["exclude_exact_target"] = True
                else:
                    mutation_count, error = _mutation_count(
                        assertion,
                        selected_before=set(selected_before),
                        selected_after=set(selected_after),
                        mutations=mutations,
                    )
                if error is not None:
                    unsupported.append(UnsupportedBaselineAssertion(assertion.id, fact_key, error))
                    continue
                assertion_facts[fact_key] = mutation_count
                for key in selected_after:
                    facts_by_key[key][fact_key] = mutation_count
                continue

            if fact_key == "delta_from_baseline":
                delta = len(selected_after) - len(selected_before)
                assertion_facts[fact_key] = delta
                for key in selected_after:
                    facts_by_key[key][fact_key] = delta
                continue

            if fact_key == "count":
                count = len(selected_after)
                assertion_facts[fact_key] = count
                for key in selected_after:
                    facts_by_key[key][fact_key] = count

        for key, facts in facts_by_key.items():
            for fact_key, value in facts.items():
                existing = enriched_fields[key].get(fact_key, _MISSING)
                if existing is not _MISSING and existing != value:
                    unsupported.append(
                        UnsupportedBaselineAssertion(
                            assertion.id,
                            fact_key,
                            "canonical resource already contains a conflicting value",
                        )
                    )
                    continue
                enriched_fields[key][fact_key] = value
                assertion_facts.setdefault(fact_key, value)
        if assertion_facts:
            derived_facts[assertion.id] = assertion_facts

    resources = tuple(
        CanonicalResource(
            provider_role=resource.provider_role,
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            fields=enriched_fields[key],
        )
        for key, resource in sorted(after_index.items())
    )
    return BaselineEnrichmentResult(
        resources=resources,
        unsupported=tuple(unsupported),
        derived_facts=derived_facts,
    )


def baseline_constructs(assertion: StateAssertionSpec) -> frozenset[str]:
    """Return the baseline/helper constructs requested by an assertion."""

    return frozenset(key for key in assertion.expected if key in _BASELINE_FACT_KEYS)


def _requested_baseline_facts(assertion: StateAssertionSpec) -> frozenset[str]:
    return baseline_constructs(assertion)


def _resource_index(
    resources: Sequence[CanonicalResource],
    *,
    label: str,
) -> dict[tuple[str, str, str], CanonicalResource]:
    indexed: dict[tuple[str, str, str], CanonicalResource] = {}
    for resource in resources:
        key = (resource.provider_role, resource.resource_type, resource.resource_id)
        if key in indexed:
            raise ValueError(f"{label} resources contain duplicate canonical identity {key!r}")
        indexed[key] = resource
    return indexed


def _select_resources(
    assertion: StateAssertionSpec,
    resources: Mapping[tuple[str, str, str], CanonicalResource],
    before: Mapping[tuple[str, str, str], CanonicalResource],
) -> tuple[dict[tuple[str, str, str], CanonicalResource], str | None]:
    selected: dict[tuple[str, str, str], CanonicalResource] = {}
    for key, resource in resources.items():
        if resource.provider_role != assertion.provider_role:
            continue
        if resource.resource_type != assertion.resource_type:
            continue
        result = _selector_matches(
            assertion.selector,
            resource.match_document(),
            existed_at_baseline=key in before,
        )
        if result.unsupported_reason is not None:
            return {}, result.unsupported_reason
        if result.matches:
            selected[key] = resource
    return selected, None


def _selector_matches(
    selector: Mapping[str, Any],
    document: Mapping[str, Any],
    *,
    existed_at_baseline: bool,
) -> _SelectorResult:
    for key, expected in selector.items():
        if key == "baseline_only":
            if not isinstance(expected, bool):
                return _SelectorResult(False, "baseline_only must be boolean")
            if expected != existed_at_baseline:
                return _SelectorResult(False)
            continue
        if key == "exclude_exact_target":
            return _SelectorResult(
                False,
                "exclude_exact_target does not identify the excluded resource",
            )
        if key == "exclude":
            if not isinstance(expected, dict):
                return _SelectorResult(False, "exclude must be an object")
            nested = _selector_matches(
                cast(dict[str, Any], expected),
                document,
                existed_at_baseline=existed_at_baseline,
            )
            if nested.unsupported_reason is not None:
                return nested
            if nested.matches:
                return _SelectorResult(False)
            continue
        if key == "number_lte":
            actual = document.get("number")
            if not _is_number(actual) or not _is_number(expected):
                return _SelectorResult(False)
            if cast(float, actual) > cast(float, expected):
                return _SelectorResult(False)
            continue
        if key == "exclude_number":
            if document.get("number") == expected:
                return _SelectorResult(False)
            continue
        if key.endswith("_in"):
            actual_key = key.removesuffix("_in")
            if not isinstance(expected, list):
                return _SelectorResult(False, f"{key} must be a list")
            if document.get(actual_key) not in expected:
                return _SelectorResult(False)
            continue
        if key.endswith("_contains"):
            actual_key = key.removesuffix("_contains")
            actual = document.get(actual_key)
            if not isinstance(actual, str) or not isinstance(expected, str) or expected not in actual:
                return _SelectorResult(False)
            continue
        if key.endswith("_pattern"):
            actual_key = key.removesuffix("_pattern")
            actual = document.get(actual_key)
            if not isinstance(actual, str) or not isinstance(expected, str):
                return _SelectorResult(False)
            try:
                if re.fullmatch(expected, actual) is None:
                    return _SelectorResult(False)
            except re.error as error:
                return _SelectorResult(False, f"{key} is not a valid regular expression: {error}")
            continue
        if not _is_subset(expected, document.get(key, _MISSING)):
            return _SelectorResult(False)
    return _SelectorResult(True)


def _resolve_exact_target_exclusion(
    assertion: StateAssertionSpec,
    *,
    assertions: Sequence[StateAssertionSpec],
    before: Mapping[tuple[str, str, str], CanonicalResource],
    after: Mapping[tuple[str, str, str], CanonicalResource],
) -> tuple[_ResolvedExactTarget | None, str | None]:
    if assertion.selector != {"exclude_exact_target": True}:
        return (
            None,
            "exclude_exact_target is supported only as the sole boolean-true selector",
        )
    if assertion.resource_type != "event_collection":
        return (
            None,
            "exclude_exact_target is supported only for an event_collection",
        )

    candidates: list[
        tuple[
            tuple[str, str, str],
            CanonicalResource,
        ]
    ] = []
    for target_assertion in assertions:
        if (
            target_assertion is assertion
            or target_assertion.provider_role != assertion.provider_role
            or target_assertion.resource_type != "event"
            or target_assertion.cardinality != 1
            or baseline_constructs(target_assertion)
        ):
            continue
        selected_before, before_error = _select_resources(
            target_assertion,
            before,
            before,
        )
        if before_error is not None:
            continue
        if len(selected_before) == 1:
            target_key = next(iter(selected_before))
            candidates.append(
                (
                    target_key,
                    selected_before[target_key],
                )
            )
    if len(candidates) != 1:
        return (
            None,
            "exclude_exact_target requires exactly one sibling event assertion "
            "that resolves to one canonical baseline event",
        )

    target_key, old_target = candidates[0]
    old_calendar = _calendar_name(old_target)
    if old_calendar is None:
        return (
            None,
            "exclude_exact_target requires a calendar identity on the target event",
        )

    selected_before = _resources_with_fields(
        before,
        provider_role=assertion.provider_role,
        resource_type="event_collection",
        fields={"calendar": old_calendar},
    )
    selected_after = _resources_with_fields(
        after,
        provider_role=assertion.provider_role,
        resource_type="event_collection",
        fields={"calendar": old_calendar},
    )
    if len(selected_before) != 1 or len(selected_after) > 1:
        return (
            None,
            "exclude_exact_target requires one baseline event collection and at "
            "most one after-state collection for the target calendar",
        )
    return (
        _ResolvedExactTarget(
            target_key=target_key,
            selected_before=selected_before,
            selected_after=selected_after,
        ),
        None,
    )


def _structured_equals_baseline_except(
    assertion: StateAssertionSpec,
    *,
    assertions: Sequence[StateAssertionSpec],
    before: Mapping[tuple[str, str, str], CanonicalResource],
    after: Mapping[tuple[str, str, str], CanonicalResource],
    selected_before: Mapping[tuple[str, str, str], CanonicalResource],
    selected_after: Mapping[tuple[str, str, str], CanonicalResource],
) -> tuple[bool, str | None]:
    raw = assertion.expected.get("equals_baseline_except")
    if not isinstance(raw, list) or not raw:
        return False, "equals_baseline_except must be a non-empty list of field names"
    raw_items = cast(list[object], raw)
    if not all(isinstance(item, str) and item for item in raw_items):
        return False, "equals_baseline_except must be a non-empty list of field names"
    paths = tuple(cast(list[str], raw_items))
    if len(set(paths)) != len(paths):
        return False, "equals_baseline_except paths must be unique"

    _, _, snapshot_error = _stable_snapshot_pair(selected_before, selected_after)
    if snapshot_error is not None:
        return False, snapshot_error

    if assertion.resource_type == "repository_snapshot":
        return _github_equals_except(
            assertion,
            paths=paths,
            before=before,
            after=after,
            selected_before=selected_before,
            selected_after=selected_after,
        )
    if assertion.resource_type == "project_snapshot":
        return _gitlab_equals_except(
            assertion,
            paths=paths,
            before=before,
            after=after,
            selected_before=selected_before,
            selected_after=selected_after,
        )
    if assertion.resource_type == "drive_snapshot":
        return _drive_equals_except(
            assertion,
            paths=paths,
            before=before,
            after=after,
            selected_before=selected_before,
            selected_after=selected_after,
        )
    if assertion.resource_type == "notion_snapshot":
        return _notion_equals_except(
            assertion,
            paths=paths,
            before=before,
            after=after,
            selected_before=selected_before,
            selected_after=selected_after,
        )
    if assertion.resource_type == "stripe_snapshot":
        return _stripe_equals_except(
            assertion,
            paths=paths,
            before=before,
            after=after,
            selected_before=selected_before,
            selected_after=selected_after,
        )
    if assertion.resource_type == "calendar_snapshot":
        return _calendar_equals_except(
            assertion,
            assertions=assertions,
            paths=paths,
            before=before,
            after=after,
            selected_before=selected_before,
            selected_after=selected_after,
        )
    return (
        False,
        f"structured equals_baseline_except paths are not supported for resource type {assertion.resource_type!r}",
    )


def _github_equals_except(
    assertion: StateAssertionSpec,
    *,
    paths: Sequence[str],
    before: Mapping[tuple[str, str, str], CanonicalResource],
    after: Mapping[tuple[str, str, str], CanonicalResource],
    selected_before: Mapping[tuple[str, str, str], CanonicalResource],
    selected_after: Mapping[tuple[str, str, str], CanonicalResource],
) -> tuple[bool, str | None]:
    exceptions: set[tuple[int, str]] = set()
    for path in paths:
        match = _GITHUB_PULL_EXCEPTION.fullmatch(path)
        if match is None:
            return False, f"unsupported GitHub repository exception path {path!r}"
        exceptions.add((int(match.group("number")), match.group("kind")))

    old_snapshot, new_snapshot, error = _stable_snapshot_pair(
        selected_before,
        selected_after,
    )
    if error is not None:
        return False, error
    assert old_snapshot is not None and new_snapshot is not None
    for number, kind in sorted(exceptions):
        field = "reviews_by_pull" if kind == "reviews" else "review_comments_by_pull"
        locations = ((field, str(number)), ("all_pull_artifacts", field, str(number)))
        if not any(_nested_path_exists(old_snapshot.fields, location) for location in locations):
            return (
                False,
                f"repository snapshot lacks baseline coverage for {kind} on pull request {number}",
            )
        if not any(_nested_path_exists(new_snapshot.fields, location) for location in locations):
            return (
                False,
                f"repository snapshot lacks after-state coverage for {kind} on pull request {number}",
            )

    repository = old_snapshot.resource_id

    def project(resource: CanonicalResource) -> dict[str, Any] | None:
        if (
            resource.resource_type == "pull_request_review"
            and (cast(int, resource.fields.get("pull_number")), "reviews") in exceptions
            and resource.fields.get("repository") == repository
        ):
            return None
        if (
            resource.resource_type == "pull_request_review_comment"
            and (cast(int, resource.fields.get("pull_number")), "review_comments") in exceptions
            and resource.fields.get("repository") == repository
        ):
            return None
        fields = deepcopy(resource.fields)
        if resource.resource_type == "repository_snapshot" and resource.resource_id == repository:
            for number, kind in exceptions:
                field = "reviews_by_pull" if kind == "reviews" else "review_comments_by_pull"
                _remove_nested_path(fields, (field, str(number)))
                _remove_nested_path(
                    fields,
                    ("all_pull_artifacts", field, str(number)),
                )
        return fields

    return (
        _projected_provider_state(before, assertion.provider_role, project)
        == _projected_provider_state(after, assertion.provider_role, project),
        None,
    )


def _gitlab_equals_except(
    assertion: StateAssertionSpec,
    *,
    paths: Sequence[str],
    before: Mapping[tuple[str, str, str], CanonicalResource],
    after: Mapping[tuple[str, str, str], CanonicalResource],
    selected_before: Mapping[tuple[str, str, str], CanonicalResource],
    selected_after: Mapping[tuple[str, str, str], CanonicalResource],
) -> tuple[bool, str | None]:
    merge_request_iids: set[int] = set()
    for path in paths:
        match = _GITLAB_DISCUSSION_EXCEPTION.fullmatch(path)
        if match is None:
            return False, f"unsupported GitLab project exception path {path!r}"
        merge_request_iids.add(int(match.group("number")))

    old_snapshot, new_snapshot, error = _stable_snapshot_pair(
        selected_before,
        selected_after,
    )
    if error is not None:
        return False, error
    assert old_snapshot is not None and new_snapshot is not None
    for iid in sorted(merge_request_iids):
        location = ("discussions_by_merge_request", str(iid))
        if not _nested_path_exists(old_snapshot.fields, location):
            return (
                False,
                f"project snapshot lacks baseline discussion coverage for merge request {iid}",
            )
        if not _nested_path_exists(new_snapshot.fields, location):
            return (
                False,
                f"project snapshot lacks after-state discussion coverage for merge request {iid}",
            )

    project = old_snapshot.resource_id

    def project_resource(resource: CanonicalResource) -> dict[str, Any] | None:
        if (
            resource.resource_type == "merge_request_diff_discussion"
            and resource.fields.get("project") == project
            and resource.fields.get("merge_request_iid") in merge_request_iids
        ):
            return None
        fields = deepcopy(resource.fields)
        if resource.resource_type == "project_snapshot" and resource.resource_id == project:
            for iid in merge_request_iids:
                _remove_nested_path(
                    fields,
                    ("discussions_by_merge_request", str(iid)),
                )
        return fields

    return (
        _projected_provider_state(before, assertion.provider_role, project_resource)
        == _projected_provider_state(after, assertion.provider_role, project_resource),
        None,
    )


def _drive_equals_except(
    assertion: StateAssertionSpec,
    *,
    paths: Sequence[str],
    before: Mapping[tuple[str, str, str], CanonicalResource],
    after: Mapping[tuple[str, str, str], CanonicalResource],
    selected_before: Mapping[tuple[str, str, str], CanonicalResource],
    selected_after: Mapping[tuple[str, str, str], CanonicalResource],
) -> tuple[bool, str | None]:
    selectors: list[dict[str, object]] = []
    for path in paths:
        match = _DRIVE_PERMISSION_EXCEPTION.fullmatch(path)
        if match is None:
            return False, f"unsupported Drive snapshot exception path {path!r}"
        selectors.append(
            {
                "name": match.group("title"),
                "content_marker": match.group("marker"),
            }
        )

    old_snapshot, new_snapshot, error = _stable_snapshot_pair(
        selected_before,
        selected_after,
    )
    if error is not None:
        return False, error
    assert old_snapshot is not None and new_snapshot is not None
    if "stable_digest" not in old_snapshot.fields or "stable_digest" not in new_snapshot.fields:
        return False, "Drive snapshot lacks a complete stable digest"

    target_ids: set[str] = set()
    for selector in selectors:
        old_targets = _resources_with_fields(
            before,
            provider_role=assertion.provider_role,
            resource_type="file",
            fields=selector,
        )
        new_targets = _resources_with_fields(
            after,
            provider_role=assertion.provider_role,
            resource_type="file",
            fields=selector,
        )
        target_key, target_error = _stable_unique_resource_key(
            old_targets,
            new_targets,
            label=f"Drive file selector {selector!r}",
        )
        if target_error is not None:
            return False, target_error
        assert target_key is not None
        if "permissions" not in old_targets[target_key].fields or "permissions" not in new_targets[target_key].fields:
            return False, "Drive target file lacks complete permission coverage"
        target_ids.add(target_key[2])

    def project(resource: CanonicalResource) -> dict[str, Any] | None:
        if resource.resource_type == "file_permission" and resource.fields.get("file_id") in target_ids:
            return None
        fields = deepcopy(resource.fields)
        if resource.resource_type == "file" and resource.resource_id in target_ids:
            fields.pop("permissions", None)
        if (
            resource.resource_type in {"drive_snapshot", "file_collection", "provider_state"}
            and resource.fields.get("scope") == "all_files"
        ):
            fields.pop("stable_digest", None)
        return fields

    return (
        _projected_provider_state(before, assertion.provider_role, project)
        == _projected_provider_state(after, assertion.provider_role, project),
        None,
    )


def _notion_equals_except(
    assertion: StateAssertionSpec,
    *,
    paths: Sequence[str],
    before: Mapping[tuple[str, str, str], CanonicalResource],
    after: Mapping[tuple[str, str, str], CanonicalResource],
    selected_before: Mapping[tuple[str, str, str], CanonicalResource],
    selected_after: Mapping[tuple[str, str, str], CanonicalResource],
) -> tuple[bool, str | None]:
    selectors: list[dict[str, object]] = []
    for path in paths:
        match = _NOTION_LIFECYCLE_EXCEPTION.fullmatch(path)
        if match is None:
            return False, f"unsupported Notion snapshot exception path {path!r}"
        selectors.append(
            {
                "database": match.group("database"),
                "Policy": match.group("policy"),
            }
        )

    old_snapshot, new_snapshot, error = _stable_snapshot_pair(
        selected_before,
        selected_after,
    )
    if error is not None:
        return False, error
    assert old_snapshot is not None and new_snapshot is not None
    if "stable_digest" not in old_snapshot.fields or "stable_digest" not in new_snapshot.fields:
        return False, "Notion snapshot lacks a complete stable digest"

    target_ids: set[str] = set()
    for selector in selectors:
        old_targets = _resources_with_fields(
            before,
            provider_role=assertion.provider_role,
            resource_type="database_page",
            fields=selector,
        )
        new_targets = _resources_with_fields(
            after,
            provider_role=assertion.provider_role,
            resource_type="database_page",
            fields=selector,
        )
        target_key, target_error = _stable_unique_resource_key(
            old_targets,
            new_targets,
            label=f"Notion database-page selector {selector!r}",
        )
        if target_error is not None:
            return False, target_error
        assert target_key is not None
        if "Lifecycle" not in old_targets[target_key].fields or "Lifecycle" not in new_targets[target_key].fields:
            return False, "Notion target database page lacks Lifecycle coverage"
        page_key = (assertion.provider_role, "page", target_key[2])
        old_page = before.get(page_key)
        new_page = after.get(page_key)
        if (
            old_page is None
            or new_page is None
            or not _nested_path_exists(old_page.fields, ("properties", "Lifecycle"))
            or not _nested_path_exists(new_page.fields, ("properties", "Lifecycle"))
        ):
            return False, "Notion target page lacks canonical Lifecycle property coverage"
        target_ids.add(target_key[2])

    def project(resource: CanonicalResource) -> dict[str, Any] | None:
        fields = deepcopy(resource.fields)
        if resource.resource_id in target_ids:
            if resource.resource_type == "database_page":
                fields.pop("Lifecycle", None)
                fields.pop("properties.Lifecycle.select.name", None)
                _remove_nested_path(fields, ("properties", "Lifecycle"))
            elif resource.resource_type == "page":
                _remove_nested_path(fields, ("properties", "Lifecycle"))
            elif resource.resource_type == "page_collection":
                fields.pop("stable_digest", None)
        if resource.resource_type == "notion_snapshot" and resource.fields.get("scope") == "workspace":
            fields.pop("stable_digest", None)
        return fields

    return (
        _projected_provider_state(before, assertion.provider_role, project)
        == _projected_provider_state(after, assertion.provider_role, project),
        None,
    )


def _stripe_equals_except(
    assertion: StateAssertionSpec,
    *,
    paths: Sequence[str],
    before: Mapping[tuple[str, str, str], CanonicalResource],
    after: Mapping[tuple[str, str, str], CanonicalResource],
    selected_before: Mapping[tuple[str, str, str], CanonicalResource],
    selected_after: Mapping[tuple[str, str, str], CanonicalResource],
) -> tuple[bool, str | None]:
    target_fields: dict[str, set[str]] = {}
    for path in paths:
        match = _STRIPE_PRICE_EXCEPTION.fullmatch(path)
        if match is None:
            return False, f"unsupported Stripe snapshot exception path {path!r}"
        selector: dict[str, object] = {
            "product_name": match.group("product_name"),
            "unit_amount": int(match.group("unit_amount")),
            "currency": match.group("currency"),
        }
        old_targets = _stripe_prices_matching(
            before,
            provider_role=assertion.provider_role,
            selector=selector,
        )
        new_targets = _stripe_prices_matching(
            after,
            provider_role=assertion.provider_role,
            selector=selector,
        )
        if len(old_targets) != 1:
            return (
                False,
                f"Stripe price selector {selector!r} must resolve to one baseline resource",
            )
        target_key = next(iter(old_targets))
        if len(new_targets) > 1:
            return False, None
        new_target = after.get(target_key)
        if new_target is not None and (
            match.group("field") not in old_targets[target_key].fields or match.group("field") not in new_target.fields
        ):
            return False, "Stripe target price lacks complete field coverage"
        target_fields.setdefault(target_key[2], set()).add(match.group("field"))

    old_snapshot, new_snapshot, error = _stable_snapshot_pair(
        selected_before,
        selected_after,
    )
    if error is not None:
        return False, error
    assert old_snapshot is not None and new_snapshot is not None
    required_digests = {"customers_digest", "products_digest", "prices_digest"}
    if not required_digests <= set(old_snapshot.fields) or not required_digests <= set(new_snapshot.fields):
        return False, "Stripe catalog snapshot lacks complete customer, product, and price digests"

    def project(resource: CanonicalResource) -> dict[str, Any] | None:
        fields = deepcopy(resource.fields)
        if resource.resource_type == "price" and resource.resource_id in target_fields:
            for field_name in target_fields[resource.resource_id]:
                fields.pop(field_name, None)
        if resource.resource_type == "stripe_snapshot" and resource.fields.get("scope") in {
            "all",
            "catalog_and_customers",
        }:
            fields.pop("prices_digest", None)
        return fields

    return (
        _projected_provider_state(before, assertion.provider_role, project)
        == _projected_provider_state(after, assertion.provider_role, project),
        None,
    )


def _calendar_equals_except(
    assertion: StateAssertionSpec,
    *,
    assertions: Sequence[StateAssertionSpec],
    paths: Sequence[str],
    before: Mapping[tuple[str, str, str], CanonicalResource],
    after: Mapping[tuple[str, str, str], CanonicalResource],
    selected_before: Mapping[tuple[str, str, str], CanonicalResource],
    selected_after: Mapping[tuple[str, str, str], CanonicalResource],
) -> tuple[bool, str | None]:
    if len(paths) != 1:
        return False, "Calendar append preservation supports exactly one declared event"
    match = _CALENDAR_APPEND_EXCEPTION.fullmatch(paths[0])
    if match is None:
        return False, f"unsupported Calendar snapshot exception path {paths[0]!r}"
    calendar = match.group("calendar")

    old_snapshot, new_snapshot, error = _stable_snapshot_pair(
        selected_before,
        selected_after,
    )
    if error is not None:
        return False, error
    assert old_snapshot is not None and new_snapshot is not None
    if "stable_digest" not in old_snapshot.fields or "stable_digest" not in new_snapshot.fields:
        return False, "Calendar snapshot lacks a complete stable digest"

    target_assertions: list[StateAssertionSpec] = []
    for target_assertion in assertions:
        if (
            target_assertion is assertion
            or target_assertion.provider_role != assertion.provider_role
            or target_assertion.resource_type != "event"
            or target_assertion.cardinality != 1
            or baseline_constructs(target_assertion)
        ):
            continue
        declared_calendar = target_assertion.selector.get(
            "calendar",
            target_assertion.selector.get("calendar_name"),
        )
        if declared_calendar != calendar:
            continue
        target_assertions.append(target_assertion)
    if len(target_assertions) != 1:
        return (
            False,
            "Calendar append exception requires exactly one sibling event assertion for the named calendar",
        )
    target_assertion = target_assertions[0]
    selected_old, old_error = _select_resources(target_assertion, before, before)
    selected_new, new_error = _select_resources(target_assertion, after, before)
    if old_error is not None or new_error is not None:
        return False, old_error or new_error
    if selected_old:
        return (
            False,
            "Calendar append target unexpectedly exists in the baseline snapshot",
        )
    if len(selected_new) > 1:
        return False, None
    target_key = next(iter(selected_new)) if selected_new else None

    old_collections = _resources_with_fields(
        before,
        provider_role=assertion.provider_role,
        resource_type="event_collection",
        fields={"calendar": calendar},
    )
    new_collections = _resources_with_fields(
        after,
        provider_role=assertion.provider_role,
        resource_type="event_collection",
        fields={"calendar": calendar},
    )
    _, collection_error = _stable_unique_resource_key(
        old_collections,
        new_collections,
        label=f"Calendar event collection {calendar!r}",
    )
    if collection_error is not None:
        return False, collection_error

    def project(resource: CanonicalResource) -> dict[str, Any] | None:
        key = (resource.provider_role, resource.resource_type, resource.resource_id)
        if target_key is not None and key == target_key:
            return None
        fields = deepcopy(resource.fields)
        if resource.resource_type == "event_collection" and resource.fields.get("calendar") == calendar:
            fields.pop("event_count", None)
            fields.pop("stable_digest", None)
        if resource.resource_type in {"calendar_snapshot", "provider_state"} and resource.fields.get("scope") == "all":
            fields.pop("stable_digest", None)
        return fields

    return (
        _projected_provider_state(before, assertion.provider_role, project)
        == _projected_provider_state(after, assertion.provider_role, project),
        None,
    )


def _stable_snapshot_pair(
    selected_before: Mapping[tuple[str, str, str], CanonicalResource],
    selected_after: Mapping[tuple[str, str, str], CanonicalResource],
) -> tuple[CanonicalResource | None, CanonicalResource | None, str | None]:
    if (
        len(selected_before) != 1
        or len(selected_after) != 1
        or next(iter(selected_before)) != next(iter(selected_after))
    ):
        return (
            None,
            None,
            "structured equals_baseline_except requires exactly one stable snapshot resource before and after",
        )
    key = next(iter(selected_before))
    return selected_before[key], selected_after[key], None


def _stable_unique_resource_key(
    selected_before: Mapping[tuple[str, str, str], CanonicalResource],
    selected_after: Mapping[tuple[str, str, str], CanonicalResource],
    *,
    label: str,
) -> tuple[tuple[str, str, str] | None, str | None]:
    if (
        len(selected_before) != 1
        or len(selected_after) != 1
        or next(iter(selected_before)) != next(iter(selected_after))
    ):
        return None, f"{label} must resolve to one stable canonical resource"
    return next(iter(selected_before)), None


def _resources_with_fields(
    resources: Mapping[tuple[str, str, str], CanonicalResource],
    *,
    provider_role: str,
    resource_type: str,
    fields: Mapping[str, object],
) -> dict[tuple[str, str, str], CanonicalResource]:
    return {
        key: resource
        for key, resource in resources.items()
        if resource.provider_role == provider_role
        and resource.resource_type == resource_type
        and all(
            field_name in resource.match_document() and _is_subset(value, resource.match_document()[field_name])
            for field_name, value in fields.items()
        )
    }


def _stripe_prices_matching(
    resources: Mapping[tuple[str, str, str], CanonicalResource],
    *,
    provider_role: str,
    selector: Mapping[str, object],
) -> dict[tuple[str, str, str], CanonicalResource]:
    product_name = selector.get("product_name")
    unit_amount = selector.get("unit_amount")
    currency = selector.get("currency")
    matches: dict[tuple[str, str, str], CanonicalResource] = {}
    for key, resource in resources.items():
        if resource.provider_role != provider_role or resource.resource_type != "price":
            continue
        if resource.fields.get("unit_amount") != unit_amount or resource.fields.get("currency") != currency:
            continue
        direct_name = resource.fields.get("product_name")
        product_id = resource.fields.get("product_id")
        product = resources.get((provider_role, "product", product_id)) if isinstance(product_id, str) else None
        joined_name = product.fields.get("name") if product is not None else None
        if direct_name == product_name or joined_name == product_name:
            matches[key] = resource
    return matches


def _projected_provider_state(
    resources: Mapping[tuple[str, str, str], CanonicalResource],
    provider_role: str,
    projector: Callable[[CanonicalResource], dict[str, Any] | None],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    projected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for key, resource in resources.items():
        if resource.provider_role != provider_role:
            continue
        fields = projector(resource)
        if fields is not None:
            projected[key] = fields
    return projected


def _nested_path_exists(fields: Mapping[str, Any], path: Sequence[str]) -> bool:
    value: object = fields
    for part in path:
        if not isinstance(value, dict):
            return False
        value_mapping = cast(dict[str, object], value)
        if part not in value_mapping:
            return False
        value = value_mapping[part]
    return True


def _remove_nested_path(fields: dict[str, Any], path: Sequence[str]) -> None:
    if not path:
        return
    value = fields
    for part in path[:-1]:
        child = value.get(part)
        if not isinstance(child, dict):
            return
        value = cast(dict[str, Any], child)
    value.pop(path[-1], None)


def _calendar_name(resource: CanonicalResource) -> str | None:
    value = resource.fields.get("calendar")
    if not isinstance(value, str):
        value = resource.fields.get("calendar_name")
    return value if isinstance(value, str) and value else None


def _simple_except_fields(assertion: StateAssertionSpec) -> tuple[tuple[str, ...], str | None]:
    raw = assertion.expected.get("except", [])
    if not isinstance(raw, list):
        return (), "except must be a list of field names"
    raw_items = cast(list[object], raw)
    if not all(isinstance(item, str) for item in raw_items):
        return (), "except must be a list of field names"
    fields = tuple(cast(list[str], raw_items))
    if any(_SIMPLE_FIELD_PATH.fullmatch(item) is None for item in fields):
        return (), "except supports only explicit top-level canonical field names"
    return fields, None


def _simple_equals_baseline_except(
    assertion: StateAssertionSpec,
) -> tuple[tuple[str, ...], str | None]:
    raw = assertion.expected.get("equals_baseline_except")
    if not isinstance(raw, list) or not raw:
        return (), "equals_baseline_except must be a non-empty list of field names"
    raw_items = cast(list[object], raw)
    if not all(isinstance(item, str) for item in raw_items):
        return (), "equals_baseline_except must be a non-empty list of field names"
    fields = tuple(cast(list[str], raw_items))
    if any(_SIMPLE_FIELD_PATH.fullmatch(item) is None for item in fields):
        return (
            (),
            "cross-resource or structured exception paths are not provable from one canonical resource",
        )
    return fields, None


def _without_fields(fields: Mapping[str, Any], excluded: Sequence[str]) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if key not in excluded}


def _mutation_count(
    assertion: StateAssertionSpec,
    *,
    selected_before: set[tuple[str, str, str]],
    selected_after: set[tuple[str, str, str]],
    mutations: Sequence[Mutation],
) -> tuple[int, str | None]:
    scope = assertion.selector.get("scope")
    if assertion.resource_type == "provider_state" and scope in _WHOLE_PROVIDER_SCOPES:
        return sum(mutation.twin == assertion.provider_role for mutation in mutations), None

    identities = selected_before | selected_after
    if not identities:
        return 0, None
    count = sum(
        (
            mutation.twin,
            mutation.resource_type,
            mutation.resource_id,
        )
        in identities
        for mutation in mutations
    )
    return count, None


def _mutation_count_excluding_exact_target(
    assertion: StateAssertionSpec,
    *,
    target_key: tuple[str, str, str],
    mutations: Sequence[Mutation],
) -> int:
    return sum(
        mutation.twin == assertion.provider_role
        and mutation.resource_type == "event"
        and (mutation.twin, mutation.resource_type, mutation.resource_id) != target_key
        for mutation in mutations
    )


def _is_subset(expected: object, actual: object) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        expected_mapping = cast(dict[object, object], expected)
        actual_mapping = cast(dict[object, object], actual)
        return all(
            key in actual_mapping and _is_subset(value, actual_mapping[key]) for key, value in expected_mapping.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and expected == actual
    return expected == actual


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


_MISSING = object()


__all__ = [
    "BaselineEnrichmentResult",
    "UnsupportedBaselineAssertion",
    "baseline_constructs",
    "enrich_baseline_semantics",
]
