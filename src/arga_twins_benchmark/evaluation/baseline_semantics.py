from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
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
    derived_facts: Mapping[str, Mapping[str, Any]] = field(
        default_factory=lambda: dict[str, Mapping[str, Any]]()
    )


@dataclass(frozen=True)
class _SelectorResult:
    matches: bool
    unsupported_reason: str | None = None


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

        selected_before, before_error = _select_resources(assertion, before_index, before_index)
        selected_after, after_error = _select_resources(assertion, after_index, before_index)
        selector_error = before_error or after_error
        if selector_error is not None:
            unsupported.extend(
                UnsupportedBaselineAssertion(assertion.id, key, selector_error)
                for key in sorted(requested)
            )
            continue

        facts_by_key: dict[tuple[str, str, str], dict[str, Any]] = {
            key: {} for key in selected_after
        }
        assertion_facts: dict[str, Any] = {}

        for fact_key in sorted(requested):
            if fact_key == "new_since_baseline":
                for key in selected_after:
                    facts_by_key[key][fact_key] = key not in before_index
                continue

            if fact_key == "equals_baseline":
                for key in selected_after:
                    old = before_index.get(key)
                    facts_by_key[key][fact_key] = (
                        old is not None and old.fields == after_index[key].fields
                    )
                continue

            if fact_key == "fields_equal_baseline":
                except_fields, error = _simple_except_fields(assertion)
                if error is not None:
                    unsupported.append(
                        UnsupportedBaselineAssertion(assertion.id, fact_key, error)
                    )
                    continue
                for key in selected_after:
                    old = before_index.get(key)
                    facts_by_key[key][fact_key] = (
                        old is not None
                        and _without_fields(old.fields, except_fields)
                        == _without_fields(after_index[key].fields, except_fields)
                    )
                    if "except" in assertion.expected:
                        facts_by_key[key]["except"] = list(except_fields)
                continue

            if fact_key == "equals_baseline_except":
                except_fields, error = _simple_equals_baseline_except(assertion)
                if error is not None:
                    unsupported.append(
                        UnsupportedBaselineAssertion(assertion.id, fact_key, error)
                    )
                    continue
                expected_value = assertion.expected[fact_key]
                for key in selected_after:
                    old = before_index.get(key)
                    equal = (
                        old is not None
                        and _without_fields(old.fields, except_fields)
                        == _without_fields(after_index[key].fields, except_fields)
                    )
                    if equal:
                        facts_by_key[key][fact_key] = expected_value
                continue

            if fact_key == "mutation_count":
                mutation_count, error = _mutation_count(
                    assertion,
                    selected_before=set(selected_before),
                    selected_after=set(selected_after),
                    mutations=mutations,
                )
                if error is not None:
                    unsupported.append(
                        UnsupportedBaselineAssertion(assertion.id, fact_key, error)
                    )
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


def _is_subset(expected: object, actual: object) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        expected_mapping = cast(dict[object, object], expected)
        actual_mapping = cast(dict[object, object], actual)
        return all(
            key in actual_mapping and _is_subset(value, actual_mapping[key])
            for key, value in expected_mapping.items()
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
