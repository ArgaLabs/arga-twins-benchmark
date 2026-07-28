from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from arga_twins_benchmark.evaluation.admin_delta_claims import (
    claim_provider_admin_deltas,
)
from arga_twins_benchmark.evaluation.baseline_semantics import enrich_baseline_semantics
from arga_twins_benchmark.evaluation.deterministic import (
    CanonicalResource,
    is_create_delete_canonical_context,
    state_document_matches,
    state_fact_matches,
)
from arga_twins_benchmark.evaluation.protocol import JsonValue, Mutation
from arga_twins_benchmark.evaluation.snapshot_enrichment import (
    enrich_snapshot_from_trusted_state,
)
from arga_twins_benchmark.evaluation.state_capture import (
    RawStateDelta,
    SnapshotCanonicalizer,
    StateCaptureError,
    TrustedStateSnapshot,
    canonicalize_query_results,
    diff_canonical_resources,
    diff_trusted_states,
)
from arga_twins_benchmark.specs.models import (
    MutationMatcherSpec,
    StateAssertionSpec,
    VerificationSpec,
)


class StateEvidenceError(StateCaptureError):
    """Raised when snapshots cannot prove complete deterministic state evidence."""


@dataclass(frozen=True)
class CanonicalizerCoverage:
    """Resource kinds a canonicalizer can authoritatively project.

    Coverage is declared separately from a concrete response because an empty
    but complete collection must still prove that a resource does not exist.
    """

    resource_types: frozenset[str]


@dataclass(frozen=True)
class DeterministicStateEvidence:
    """Trusted state inputs ready to pass to ``evaluate_deterministic``."""

    baseline_resources: tuple[CanonicalResource, ...]
    resources: tuple[CanonicalResource, ...]
    mutations: tuple[Mutation, ...]
    raw_delta_count: int
    canonical_delta_count: int
    projection_delta_count: int
    derived_facts: Mapping[str, Mapping[str, Any]] = field(default_factory=lambda: dict[str, Mapping[str, Any]]())


_MESSAGE_TYPES = frozenset({"message", "message_collection"})
_DRIVE_TYPES = frozenset(
    {
        "file",
        "file_permission",
        "folder",
        "drive_snapshot",
        "file_collection",
        "provider_state",
    }
)
_GITHUB_PULL_TYPES = frozenset({"pull_request", "pull_request_collection", "repository_snapshot"})
_GITHUB_REVIEW_TYPES = frozenset(
    {
        "pull_request",
        "pull_request_collection",
        "pull_request_review",
        "pull_request_review_comment",
        "repository_snapshot",
    }
)
_JIRA_TYPES = frozenset({"issue", "issue_comment", "issue_collection"})
_LINEAR_TYPES = frozenset({"project", "issue", "issue_comment", "issue_collection"})
_NOTION_TYPES = frozenset({"page", "page_collection", "page_markdown", "database_page", "notion_snapshot"})
_CALENDAR_EVENT_TYPES = frozenset({"event", "event_collection", "calendar_snapshot", "provider_state"})


STANDARD_CANONICALIZER_COVERAGE: Mapping[str, CanonicalizerCoverage] = {
    "discord_guild_channels_messages_stable": CanonicalizerCoverage(_MESSAGE_TYPES),
    "discord_guild_channels_messages_v1": CanonicalizerCoverage(_MESSAGE_TYPES),
    "drive_files_content_hash_v1": CanonicalizerCoverage(_DRIVE_TYPES),
    "github_all_pull_review_artifacts": CanonicalizerCoverage(_GITHUB_REVIEW_TYPES),
    "github_contents_recursive_hashes": CanonicalizerCoverage(frozenset({"file", "repository_snapshot"})),
    "github_file_and_ref_stable": CanonicalizerCoverage(frozenset({"file"})),
    "github_pull_and_repo_tree_v1": CanonicalizerCoverage(
        frozenset({"pull_request", "pull_request_collection", "repository"})
    ),
    "github_pulls_stable": CanonicalizerCoverage(_GITHUB_PULL_TYPES),
    "github_refs_stable": CanonicalizerCoverage(frozenset({"git_ref", "repository_snapshot"})),
    "github_repository_and_pulls_stable": CanonicalizerCoverage(_GITHUB_PULL_TYPES),
    "github_repository_stable": CanonicalizerCoverage(frozenset({"repository_snapshot"})),
    "github_review_comments_stable": CanonicalizerCoverage(
        frozenset({"pull_request_review_comment", "repository_snapshot"})
    ),
    "github_reviews_stable": CanonicalizerCoverage(frozenset({"pull_request_review", "repository_snapshot"})),
    "gitlab_branches_stable": CanonicalizerCoverage(frozenset({"branch", "project_snapshot"})),
    "gitlab_discussions_stable": CanonicalizerCoverage(
        frozenset({"merge_request_diff_discussion", "project_snapshot"})
    ),
    "gitlab_file_and_branch_stable": CanonicalizerCoverage(frozenset({"file"})),
    "gitlab_file_hash": CanonicalizerCoverage(frozenset({"file", "project_snapshot"})),
    "gitlab_merge_request_and_tree_v1": CanonicalizerCoverage(frozenset({"merge_request", "project"})),
    "gitlab_merge_requests_stable": CanonicalizerCoverage(frozenset({"merge_request", "project_snapshot"})),
    "gitlab_project_and_merge_requests_stable": CanonicalizerCoverage(frozenset({"merge_request", "project_snapshot"})),
    "gitlab_project_stable": CanonicalizerCoverage(frozenset({"project_snapshot"})),
    "gmail_drafts_stable": CanonicalizerCoverage(frozenset({"draft", "gmail_snapshot", "provider_state"})),
    "gmail_drafts_v1": CanonicalizerCoverage(frozenset({"draft", "gmail_snapshot", "provider_state"})),
    "gmail_labels_stable": CanonicalizerCoverage(frozenset({"label", "gmail_snapshot", "provider_state"})),
    "gmail_labels_v1": CanonicalizerCoverage(frozenset({"label", "gmail_snapshot", "provider_state"})),
    "gmail_messages_labels_threads_v1": CanonicalizerCoverage(
        frozenset({"message", "message_or_draft", "gmail_snapshot", "provider_state"})
    ),
    "gmail_messages_stable": CanonicalizerCoverage(
        frozenset({"message", "message_or_draft", "gmail_snapshot", "provider_state"})
    ),
    "gmail_messages_threads_v1": CanonicalizerCoverage(
        frozenset({"message", "message_or_draft", "gmail_snapshot", "provider_state"})
    ),
    "gmail_threads_stable": CanonicalizerCoverage(frozenset({"thread", "gmail_snapshot", "provider_state"})),
    "google_calendar_all_events_v1": CanonicalizerCoverage(_CALENDAR_EVENT_TYPES),
    "google_calendar_events_v1": CanonicalizerCoverage(_CALENDAR_EVENT_TYPES),
    "google_calendar_list_v1": CanonicalizerCoverage(frozenset({"calendar"})),
    "google_calendar_state_stable": CanonicalizerCoverage(_CALENDAR_EVENT_TYPES),
    "google_drive_state_stable": CanonicalizerCoverage(_DRIVE_TYPES),
    "jira_SEC_issues_stable": CanonicalizerCoverage(_JIRA_TYPES),
    "jira_issues_and_comments_v1": CanonicalizerCoverage(_JIRA_TYPES),
    "jira_issues_comments_stable": CanonicalizerCoverage(_JIRA_TYPES),
    "linear_issues_comments_v1": CanonicalizerCoverage(_LINEAR_TYPES),
    "linear_issues_projects_comments_v1": CanonicalizerCoverage(_LINEAR_TYPES),
    "linear_team_ENG_issues_stable": CanonicalizerCoverage(_LINEAR_TYPES),
    "linear_team_OPS_issues_stable": CanonicalizerCoverage(_LINEAR_TYPES),
    "linear_team_REL_issues_comments_stable": CanonicalizerCoverage(_LINEAR_TYPES),
    "notion_pages_complete": CanonicalizerCoverage(_NOTION_TYPES),
    "notion_pages_markdown_v1": CanonicalizerCoverage(_NOTION_TYPES),
    "notion_spec_pages_stable": CanonicalizerCoverage(_NOTION_TYPES),
    "notion_state_stable": CanonicalizerCoverage(_NOTION_TYPES),
    "slack_channels_messages_stable": CanonicalizerCoverage(_MESSAGE_TYPES),
    "slack_channels_messages_v1": CanonicalizerCoverage(_MESSAGE_TYPES),
    "stripe_customers_stable": CanonicalizerCoverage(frozenset({"customer", "stripe_snapshot"})),
    "stripe_prices_stable": CanonicalizerCoverage(frozenset({"price", "stripe_snapshot"})),
    "stripe_products_stable": CanonicalizerCoverage(frozenset({"product", "stripe_snapshot"})),
}


_PROJECTION_RESOURCE_TYPES = frozenset(
    {
        "calendar_snapshot",
        "drive_snapshot",
        "event_collection",
        "file_collection",
        "gmail_snapshot",
        "issue_collection",
        "message_collection",
        "message_or_draft",
        "notion_snapshot",
        "page_collection",
        "project_snapshot",
        "provider_state",
        "pull_request_collection",
        "repository_snapshot",
        "stripe_snapshot",
    }
)

# One provider mutation can alter both a provider-native field and a stable
# canonical convenience field. These pairs are one semantic update, not
# collateral mutations.
_UPDATE_FIELD_ECHOES: Mapping[tuple[str, str], frozenset[str]] = {
    ("database_page", "properties.Lifecycle.select.name"): frozenset({"Lifecycle"}),
    ("event", "attendees"): frozenset({"attendee_emails"}),
    # Linear updates server-maintained timestamps whenever a candidate changes
    # an issue. State transitions additionally maintain the provider-native
    # state identity and lifecycle timestamps. These are consequences of the
    # declared business mutation, not independently candidate-controlled
    # fields. Keep the list field-specific so an unrelated title, project, or
    # assignee change remains visible to default-deny.
    ("issue", "description"): frozenset({"updated_at"}),
    ("issue", "priority"): frozenset({"updated_at"}),
    ("issue", "state"): frozenset(
        {
            "canceled_at",
            "completed_at",
            "started_at",
            "state_id",
            "state_type",
            "updated_at",
        }
    ),
    ("issue", "status"): frozenset({"status_category", "status_type"}),
    ("message", "labelIds"): frozenset({"labels", "labels_contain", "Needs-Finance", "Needs-Finance_count"}),
}

_BASELINE_PULL_REFERENCE = re.compile(r"^baseline\.pull\[(\d+)\]\.head\.sha$")
_BASELINE_MERGE_REQUEST_REFERENCE = re.compile(r"^baseline\.merge_request\[(\d+)\]\.sha$")
_ASSERTION_REFERENCE = re.compile(r"^(?P<assertion>[A-Za-z0-9_.-]+)\.(?P<field>[A-Za-z0-9_.-]+)$")
_HELPER_EXPECTED_KEYS = frozenset(
    {
        "count",
        "delta_from_baseline",
        "equals_baseline",
        "equals_baseline_except",
        "fields_equal_baseline",
        "mutation_count",
        "new_since_baseline",
    }
)
_ISSUE_COLLECTION_AGGREGATE_SELECTOR_FIELDS = frozenset(
    {
        "issues",
        "markers",
        "project_key",
        "resource_id",
        "scope",
        "team_key",
    }
)
_ISSUE_COLLECTION_MEMBER_IDENTITY_FIELDS = ("identifier", "key", "id")


def build_deterministic_state_evidence(
    *,
    baseline: TrustedStateSnapshot,
    final: TrustedStateSnapshot,
    verification: VerificationSpec,
    canonicalizers: Mapping[str, SnapshotCanonicalizer] | None = None,
    coverage: Mapping[str, CanonicalizerCoverage] = STANDARD_CANONICALIZER_COVERAGE,
) -> DeterministicStateEvidence:
    """Build fail-closed canonical state evidence for one benchmark episode.

    The two snapshots must have the exact query contract declared by the
    verifier. Canonicalizers must explicitly declare coverage for every state
    assertion and mutation resource type. Unsupported baseline or relational
    constructs raise ``StateEvidenceError`` rather than producing a partial
    state grade.
    """

    if canonicalizers is None:
        # Imported lazily to avoid a state_capture -> registry import cycle.
        from arga_twins_benchmark.evaluation.canonicalizers import CANONICALIZERS

        canonicalizers = CANONICALIZERS

    _validate_snapshot_contracts(
        baseline=baseline,
        final=final,
        verification=verification,
        canonicalizers=canonicalizers,
        coverage=coverage,
    )
    raw_deltas = diff_trusted_states(baseline, final)
    enriched_baseline = enrich_snapshot_from_trusted_state(baseline)
    enriched_final = enrich_snapshot_from_trusted_state(final)
    before_resources = canonicalize_query_results(
        enriched_baseline,
        canonicalizers=canonicalizers,
    )
    after_resources = canonicalize_query_results(
        enriched_final,
        canonicalizers=canonicalizers,
    )
    assertions = verification.deterministic.state_assertions
    before_resources = _materialize_issue_collection_member_projections(
        before_resources,
        assertions=assertions,
    )
    after_resources = _materialize_issue_collection_member_projections(
        after_resources,
        assertions=assertions,
    )
    all_canonical_mutations = diff_canonical_resources(before_resources, after_resources)
    mutations, projection_count = _semantic_mutations(
        before=before_resources,
        after=after_resources,
        verification=verification,
    )
    synthetic_mutations = _validate_raw_delta_representation(
        raw_deltas=raw_deltas,
        canonical_mutations=all_canonical_mutations,
    )
    mutations.extend(synthetic_mutations)

    enrichment = enrich_baseline_semantics(
        before=before_resources,
        after=after_resources,
        mutations=mutations,
        assertions=assertions,
    )
    if enrichment.unsupported:
        details = "; ".join(f"{item.assertion_id}.{item.construct}: {item.reason}" for item in enrichment.unsupported)
        raise StateEvidenceError(f"unsupported baseline state semantics: {details}")
    assertion_resources = _materialize_assertion_state_proofs(
        before=before_resources,
        after=enrichment.resources,
        final=final,
        verification=verification,
    )
    # Selector operators such as ``number_lte`` are synthetic proofs used only
    # by the final assertion matcher. Materializing them before baseline
    # comparison makes an unchanged resource appear changed because the proof
    # field exists only in the after projection. Derive preservation facts
    # first, then attach selector proofs.
    selector_resources = _materialize_selector_proofs(
        before=before_resources,
        after=assertion_resources,
        assertions=assertions,
    )
    final_resources = _materialize_relational_proofs(
        before=before_resources,
        after=selector_resources,
        assertions=assertions,
    )
    return DeterministicStateEvidence(
        baseline_resources=tuple(before_resources),
        resources=tuple(final_resources),
        mutations=tuple(mutations),
        raw_delta_count=len(raw_deltas),
        canonical_delta_count=len(all_canonical_mutations),
        projection_delta_count=projection_count,
        derived_facts=enrichment.derived_facts,
    )


def _materialize_issue_collection_member_projections(
    resources: Sequence[CanonicalResource],
    *,
    assertions: Sequence[StateAssertionSpec],
) -> list[CanonicalResource]:
    """Project stable issue members when a collection assertion selects them.

    Jira and Linear canonicalizers expose one authoritative aggregate per
    project or team. Selectors such as ``summary_contains: REL-250`` and
    ``team_key: OPS, marker: INC-419`` describe members of those aggregates,
    not the aggregate object. Synthetic members retain the
    ``issue_collection`` type so collection count and baseline semantics can
    operate without turning projection changes into candidate mutations.

    Pairing is deliberately fail-closed: every member needs a stable provider
    identity and that identity may occur only once in the applicable
    collection surface.
    """

    member_roles = {
        assertion.provider_role
        for assertion in assertions
        if assertion.resource_type == "issue_collection"
        and _issue_collection_selector_targets_members(assertion.selector)
    }
    if not member_roles:
        return list(resources)

    projected: list[CanonicalResource] = []
    seen: dict[tuple[str, str, str], str] = {}
    for collection in resources:
        if collection.provider_role not in member_roles or collection.resource_type != "issue_collection":
            continue
        issues = collection.fields.get("issues")
        if not isinstance(issues, list):
            raise StateEvidenceError(
                "issue_collection member projection requires an authoritative "
                f"issues list for {collection.provider_role}/{collection.resource_id}"
            )
        for index, raw_issue in enumerate(cast(list[object], issues)):
            if not isinstance(raw_issue, dict):
                raise StateEvidenceError(
                    "issue_collection member projection found a non-object issue "
                    f"at {collection.provider_role}/{collection.resource_id}[{index}]"
                )
            issue = cast(dict[str, Any], raw_issue)
            identity = next(
                (
                    (field_name, str(value))
                    for field_name in _ISSUE_COLLECTION_MEMBER_IDENTITY_FIELDS
                    if (
                        (value := issue.get(field_name)) is not None
                        and isinstance(value, str | int)
                        and not isinstance(value, bool)
                        and str(value)
                    )
                ),
                None,
            )
            if identity is None:
                raise StateEvidenceError(
                    "issue_collection member projection requires one of "
                    f"{_ISSUE_COLLECTION_MEMBER_IDENTITY_FIELDS!r} at "
                    f"{collection.provider_role}/{collection.resource_id}[{index}]"
                )
            identity_field, identity_value = identity
            identity_key = (collection.provider_role, identity_field, identity_value)
            prior_collection = seen.get(identity_key)
            if prior_collection is not None:
                raise StateEvidenceError(
                    "issue_collection member projection has duplicate stable identity "
                    f"{identity_field}={identity_value!r} in "
                    f"{prior_collection!r} and {collection.resource_id!r}"
                )
            seen[identity_key] = collection.resource_id

            fields = dict(issue)
            for scope_field in ("project_key", "scope", "team_key"):
                if scope_field in collection.fields:
                    fields.setdefault(scope_field, collection.fields[scope_field])
            fields["collection_id"] = collection.resource_id
            fields["collection_member"] = True
            projected.append(
                CanonicalResource(
                    provider_role=collection.provider_role,
                    resource_type="issue_collection",
                    resource_id=f"member:{identity_field}:{identity_value}",
                    fields=fields,
                )
            )
    return [
        *resources,
        *sorted(
            projected,
            key=lambda resource: (
                resource.provider_role,
                resource.resource_id,
            ),
        ),
    ]


def _issue_collection_selector_targets_members(selector: Mapping[str, Any]) -> bool:
    for key, value in selector.items():
        if key in {"baseline_only", "exclude_exact_target"}:
            continue
        if key == "exclude":
            if isinstance(value, dict) and _issue_collection_selector_targets_members(cast(dict[str, Any], value)):
                return True
            continue
        if key in {"exclude_number", "number_lte"}:
            field_name = "number"
        elif key.endswith(("_contains", "_in", "_pattern")):
            field_name = key.rsplit("_", maxsplit=1)[0]
        else:
            field_name = key
        if field_name not in _ISSUE_COLLECTION_AGGREGATE_SELECTOR_FIELDS:
            return True
    return False


def _validate_snapshot_contracts(
    *,
    baseline: TrustedStateSnapshot,
    final: TrustedStateSnapshot,
    verification: VerificationSpec,
    canonicalizers: Mapping[str, SnapshotCanonicalizer],
    coverage: Mapping[str, CanonicalizerCoverage],
) -> None:
    expected = {
        query.id: (
            query.provider_role,
            query.method,
            query.path,
            query.canonicalizer,
        )
        for query in verification.deterministic.snapshot_queries
    }
    expected_ids = set(expected)
    for label, snapshot in (("baseline", baseline), ("final", final)):
        actual_ids = set(snapshot.queries)
        if actual_ids != expected_ids:
            missing = sorted(expected_ids - actual_ids)
            extra = sorted(actual_ids - expected_ids)
            raise StateEvidenceError(
                f"{label} snapshot query set differs from verifier (missing={missing}, extra={extra})"
            )
        if not snapshot.providers:
            raise StateEvidenceError(f"{label} snapshot has no trusted provider states")
        role_to_provider: dict[str, str] = {}
        for provider_name, provider in snapshot.providers.items():
            prior = role_to_provider.setdefault(provider.provider_role, provider_name)
            if prior != provider_name:
                raise StateEvidenceError(
                    f"{label} snapshot maps role {provider.provider_role!r} to more than one provider"
                )
        for query_id, contract in expected.items():
            capture = snapshot.queries[query_id]
            actual_contract = (
                capture.provider_role,
                capture.method,
                capture.path,
                capture.canonicalizer,
            )
            if actual_contract != contract:
                raise StateEvidenceError(f"{label} snapshot query {query_id!r} contract differs from verifier")
            if capture.status_code != 200:
                raise StateEvidenceError(
                    f"{label} snapshot query {query_id!r} has incomplete HTTP status {capture.status_code}"
                )
            provider = snapshot.providers.get(capture.provider_name)
            if provider is None or provider.provider_role != capture.provider_role:
                raise StateEvidenceError(
                    f"{label} snapshot query {query_id!r} is not tied to its trusted provider state"
                )
            if capture.canonicalizer not in canonicalizers:
                raise StateEvidenceError(
                    f"snapshot query {query_id!r} uses unregistered canonicalizer {capture.canonicalizer!r}"
                )
            if capture.canonicalizer not in coverage:
                raise StateEvidenceError(
                    f"snapshot query {query_id!r} canonicalizer {capture.canonicalizer!r} has no declared coverage"
                )

    if set(baseline.providers) != set(final.providers):
        raise StateEvidenceError("baseline and final snapshots contain different providers")
    for provider_name in baseline.providers:
        if baseline.providers[provider_name].provider_role != final.providers[provider_name].provider_role:
            raise StateEvidenceError(f"provider role changed for {provider_name!r} between snapshots")
    for query_id in expected:
        if baseline.queries[query_id].provider_name != final.queries[query_id].provider_name:
            raise StateEvidenceError(f"snapshot query {query_id!r} changed concrete provider")

    covered: dict[str, set[str]] = {}
    for query in verification.deterministic.snapshot_queries:
        covered.setdefault(query.provider_role, set()).update(coverage[query.canonicalizer].resource_types)
    required_types = {
        (assertion.provider_role, assertion.resource_type) for assertion in verification.deterministic.state_assertions
    }
    required_types.update(
        (rule.provider_role, rule.resource_type)
        for rule in (
            *verification.deterministic.mutation_policy.required,
            *verification.deterministic.mutation_policy.allowed,
        )
    )
    unsupported = sorted(
        f"{role}/{resource_type}"
        for role, resource_type in required_types
        if resource_type not in covered.get(role, set())
    )
    if unsupported:
        raise StateEvidenceError("snapshot canonicalizers do not cover verifier resources: " + ", ".join(unsupported))


def _validate_raw_delta_representation(
    *,
    raw_deltas: Sequence[RawStateDelta],
    canonical_mutations: Sequence[Mutation],
) -> list[Mutation]:
    entity_mutations = [
        mutation for mutation in canonical_mutations if mutation.resource_type not in _PROJECTION_RESOURCE_TYPES
    ]
    provider_claims = claim_provider_admin_deltas(
        raw_deltas=raw_deltas,
        canonical_mutations=entity_mutations,
    )
    for index, delta in enumerate(raw_deltas):
        if delta.scope != "admin":
            continue
        if index in provider_claims.claimed_indices:
            continue
        matches = [
            mutation
            for mutation in entity_mutations
            if mutation.twin == delta.provider_role and _admin_delta_matches_mutation(delta, mutation)
        ]
        if len(matches) != 1:
            raise StateEvidenceError(
                "trusted admin delta lacks one unambiguous canonical resource "
                f"representation: {delta.provider_role}{delta.json_pointer} "
                f"(matches={len(matches)})"
            )
    return list(provider_claims.synthetic_mutations)


_STABLE_EVIDENCE_FIELDS = frozenset(
    {
        "id",
        "resource_id",
        "key",
        "identifier",
        "number",
        "iid",
        "ts",
        "emailAddress",
        "email",
    }
)
_ADMIN_RESOURCE_COLLECTIONS: Mapping[str, frozenset[str]] = {
    "branch": frozenset({"branches"}),
    "customer": frozenset({"customers"}),
    "database_page": frozenset({"pages"}),
    "draft": frozenset({"drafts"}),
    "event": frozenset({"events"}),
    "file": frozenset({"files"}),
    "file_permission": frozenset({"permissions"}),
    "folder": frozenset({"files", "folders"}),
    "git_ref": frozenset({"refs"}),
    "issue": frozenset({"issues"}),
    "issue_comment": frozenset({"comments"}),
    "merge_request": frozenset({"merge_requests"}),
    "merge_request_discussion_note": frozenset({"discussions", "notes"}),
    "merge_request_diff_discussion": frozenset({"discussions"}),
    "message": frozenset({"messages"}),
    "page": frozenset({"pages"}),
    "page_markdown": frozenset({"pages"}),
    "price": frozenset({"prices"}),
    "product": frozenset({"products"}),
    "project": frozenset({"projects"}),
    "pull_request": frozenset({"pull_requests", "pulls"}),
    "pull_request_review": frozenset({"reviews"}),
    "pull_request_review_comment": frozenset({"comments", "review_comments"}),
    "repository": frozenset({"repositories", "repos"}),
}


def _admin_delta_matches_mutation(
    delta: RawStateDelta,
    mutation: Mutation,
) -> bool:
    collections = _ADMIN_RESOURCE_COLLECTIONS.get(mutation.resource_type)
    if collections is None or not collections & set(delta.path):
        return False
    delta_identities = _delta_identities(delta)
    mutation_identities = _mutation_identities(mutation)
    if not delta_identities & mutation_identities:
        return False
    if mutation.operation == "update":
        return _admin_update_matches_mutation(delta, mutation)
    raw_value = delta.after if mutation.operation == "create" else delta.before
    mutation_value = mutation.after if mutation.operation == "create" else mutation.before
    return _admin_object_is_fully_projected(
        raw_value,
        mutation_value,
        mutation_identities=mutation_identities,
    )


def _admin_object_is_fully_projected(
    raw: JsonValue,
    canonical: JsonValue,
    *,
    mutation_identities: set[tuple[str, str]],
) -> bool:
    if not isinstance(canonical, dict):
        return False
    candidates = _identity_objects(raw, identities=mutation_identities)
    if not candidates and isinstance(raw, dict):
        candidates = [cast(dict[str, JsonValue], raw)]
    if len(candidates) != 1:
        return False
    canonical_mapping = cast(dict[str, JsonValue], canonical)
    return all(
        (
            key in _STABLE_EVIDENCE_FIELDS
            and ((key, str(value)) in mutation_identities or ("*", str(value)) in mutation_identities)
        )
        or (key in canonical_mapping and canonical_mapping[key] == value)
        for key, value in candidates[0].items()
    )


def _identity_objects(
    value: JsonValue,
    *,
    identities: set[tuple[str, str]],
) -> list[dict[str, JsonValue]]:
    matches: list[dict[str, JsonValue]] = []
    if isinstance(value, dict):
        mapping = cast(dict[str, JsonValue], value)
        own = {
            (key, str(item))
            for key, item in mapping.items()
            if key in _STABLE_EVIDENCE_FIELDS and isinstance(item, str | int) and not isinstance(item, bool)
        }
        if own & identities or {("*", item) for _, item in own} & identities:
            matches.append(mapping)
        else:
            for item in mapping.values():
                matches.extend(_identity_objects(item, identities=identities))
    elif isinstance(value, list):
        for item in value:
            matches.extend(_identity_objects(item, identities=identities))
    return matches


def _admin_update_matches_mutation(
    delta: RawStateDelta,
    mutation: Mutation,
) -> bool:
    if not isinstance(mutation.before, dict) or not isinstance(mutation.after, dict):
        return False
    before = cast(dict[str, JsonValue], mutation.before)
    after = cast(dict[str, JsonValue], mutation.after)
    leaf = delta.path[-1] if delta.path else ""
    if "=" not in leaf and leaf in before | after:
        return before.get(leaf) == delta.before and after.get(leaf) == delta.after
    return _has_non_identity_overlap(delta.before, mutation.before) and _has_non_identity_overlap(
        delta.after, mutation.after
    )


def _has_non_identity_overlap(
    raw: JsonValue,
    canonical: JsonValue,
) -> bool:
    if isinstance(raw, list):
        return any(_has_non_identity_overlap(item, canonical) for item in raw)
    if not isinstance(raw, dict) or not isinstance(canonical, dict):
        return False
    raw_mapping = cast(dict[str, JsonValue], raw)
    canonical_mapping = cast(dict[str, JsonValue], canonical)
    return any(
        key not in _STABLE_EVIDENCE_FIELDS and key in canonical_mapping and value == canonical_mapping[key]
        for key, value in raw_mapping.items()
    )


def _delta_identities(delta: RawStateDelta) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    for part in delta.path:
        if "=" in part:
            key, value = part.split("=", 1)
            if key in _STABLE_EVIDENCE_FIELDS and value:
                identities.add((key, value))
                identities.add(("*", value))
        elif part:
            identities.add(("*", part))
    identities.update(_stable_value_identities(delta.before))
    identities.update(_stable_value_identities(delta.after))
    return identities


def _mutation_identities(mutation: Mutation) -> set[tuple[str, str]]:
    identities = {
        ("*", mutation.resource_id),
        ("*", _provider_id(mutation.resource_id)),
    }
    identities.update(_stable_value_identities(mutation.before))
    identities.update(_stable_value_identities(mutation.after))
    return identities


def _stable_value_identities(value: JsonValue) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                key in _STABLE_EVIDENCE_FIELDS
                and isinstance(item, str | int)
                and not isinstance(item, bool)
                and str(item)
            ):
                rendered = str(item)
                identities.add((key, rendered))
                identities.add(("*", rendered))
            identities.update(_stable_value_identities(item))
    elif isinstance(value, list):
        for item in value:
            identities.update(_stable_value_identities(item))
    return identities


def _semantic_mutations(
    *,
    before: Sequence[CanonicalResource],
    after: Sequence[CanonicalResource],
    verification: VerificationSpec,
) -> tuple[list[Mutation], int]:
    before_index = _resource_index(before)
    after_index = _resource_index(after)
    raw_mutations = diff_canonical_resources(before, after)
    required_rules = verification.deterministic.mutation_policy.required
    allowed_rules = verification.deterministic.mutation_policy.allowed
    semantic: list[Mutation] = []
    projections = 0
    governed_keys: set[tuple[str, str, str]] = set()

    for mutation in raw_mutations:
        key = (mutation.twin, mutation.resource_type, mutation.resource_id)
        if mutation.resource_type in _PROJECTION_RESOURCE_TYPES:
            projections += 1
            continue
        document_resource = after_index.get(key) if mutation.operation != "delete" else before_index.get(key)
        if document_resource is None:
            raise StateEvidenceError(f"canonical mutation {key!r} has no matching resource projection")
        matching_required = [
            rule
            for rule in required_rules
            if rule.provider_role == mutation.twin
            and rule.resource_type == mutation.resource_type
            and rule.operation == mutation.operation
            and _selector_matches(
                rule.selector,
                document_resource.match_document(),
                existed_at_baseline=key in before_index,
            )
        ]
        matching_allowed = [
            rule
            for rule in allowed_rules
            if rule.provider_role == mutation.twin
            and rule.resource_type == mutation.resource_type
            and rule.operation == mutation.operation
            and _selector_matches(
                rule.selector,
                document_resource.match_document(),
                existed_at_baseline=key in before_index,
            )
        ]
        matching_rules = matching_required or matching_allowed
        matching_rules = _compatible_projection_rules(
            matching_rules,
            mutation=mutation,
        )
        if matching_rules and _update_changes_are_governed(
            mutation,
            matching_rules=matching_rules,
        ):
            projected, residual = _project_governed_mutation(
                mutation,
                matching_rules=matching_rules,
            )
            semantic.append(projected)
            if residual is not None:
                semantic.append(residual)
            governed_keys.add(key)
        else:
            semantic.append(mutation)

    semantic, suppressed = _suppress_entity_projection_echoes(
        semantic,
        governed_keys=governed_keys,
    )
    return semantic, projections + suppressed


def _compatible_projection_rules(
    rules: Sequence[MutationMatcherSpec],
    *,
    mutation: Mutation,
) -> list[MutationMatcherSpec]:
    if len(rules) <= 1:
        return list(rules)
    contracts = {
        (
            json.dumps(rule.selector, sort_keys=True, separators=(",", ":")),
            tuple(sorted(rule.fields)),
        )
        for rule in rules
    }
    if len(contracts) != 1:
        rule_ids = ", ".join(sorted(rule.id for rule in rules))
        raise StateEvidenceError(
            "canonical mutation matches conflicting projection contracts "
            f"for {mutation.twin}/{mutation.resource_type}/{mutation.resource_id}: "
            f"{rule_ids}"
        )
    # Identical required/allowed declarations are one projection contract.
    return [rules[0]]


def _update_changes_are_governed(
    mutation: Mutation,
    *,
    matching_rules: Sequence[MutationMatcherSpec],
) -> bool:
    if mutation.operation != "update":
        return True
    if not isinstance(mutation.before, dict) or not isinstance(mutation.after, dict):
        return False
    before = cast(dict[str, JsonValue], mutation.before)
    after = cast(dict[str, JsonValue], mutation.after)
    changed = {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
    allowed = {field_name for rule in matching_rules for field_name in rule.fields}
    echoes = {
        echo
        for field_name in allowed
        for echo in _UPDATE_FIELD_ECHOES.get(
            (mutation.resource_type, field_name),
            frozenset(),
        )
    }
    return bool(changed & allowed) and changed <= allowed | echoes


def _project_governed_mutation(
    mutation: Mutation,
    *,
    matching_rules: Sequence[MutationMatcherSpec],
) -> tuple[Mutation, Mutation | None]:
    selector_fields = {key: value for rule in matching_rules for key, value in rule.selector.items()}
    allowed_fields = {field_name for rule in matching_rules for field_name in rule.fields}
    unexpected_fields: set[str] = set()

    def project(value: JsonValue) -> JsonValue:
        if not isinstance(value, dict):
            return value
        raw = cast(dict[str, JsonValue], value)
        if mutation.operation in {"create", "delete"}:
            covered_fields = allowed_fields | set(selector_fields)
            context_fields = {
                field_name
                for field_name, field_value in raw.items()
                if field_name not in covered_fields
                and is_create_delete_canonical_context(
                    resource_type=mutation.resource_type,
                    field_name=field_name,
                    value=field_value,
                    covered_fields=frozenset(covered_fields),
                )
            }
            unexpected_fields.update(set(raw) - covered_fields - context_fields)
            projected = {key: raw[key] for key in sorted(covered_fields | context_fields) if key in raw}
            for key, expected in selector_fields.items():
                if _selector_operator(key):
                    projected[key] = cast(JsonValue, expected)
            return projected
        projected = {key: raw[key] for key in sorted(allowed_fields) if key in raw}
        for key, expected in selector_fields.items():
            if _selector_operator(key):
                projected[key] = cast(JsonValue, expected)
            elif key in raw:
                projected[key] = raw[key]
        return projected

    projected = Mutation(
        twin=mutation.twin,
        resource_type=mutation.resource_type,
        resource_id=mutation.resource_id,
        operation=mutation.operation,
        before=project(mutation.before),
        after=project(mutation.after),
    )
    if not unexpected_fields:
        return projected, None

    # The projected mutation lets the declared rule count the governed
    # operation. Retaining the full canonical mutation as a second item makes
    # every unapproved field fail default-deny; it must never be silently lost
    # merely because another part of the create/delete was allowed.
    return projected, mutation


def _suppress_entity_projection_echoes(
    mutations: Sequence[Mutation],
    *,
    governed_keys: Collection[tuple[str, str, str]],
) -> tuple[list[Mutation], int]:
    governed_permissions = {
        (
            mutation.twin,
            cast(dict[str, JsonValue], mutation.after).get("file_id") if isinstance(mutation.after, dict) else None,
        )
        for mutation in mutations
        if mutation.resource_type == "file_permission"
    }
    kept: list[Mutation] = []
    suppressed = 0
    for mutation in mutations:
        if (
            mutation.operation == "update"
            and mutation.resource_type == "page"
            and _page_projection_is_exact_echo(
                mutation,
                mutations=mutations,
                governed_keys=governed_keys,
            )
        ):
            suppressed += 1
            continue
        if (
            mutation.operation == "update"
            and mutation.resource_type == "file"
            and (mutation.twin, mutation.resource_id) in governed_permissions
            and _changed_fields(mutation) <= {"permissions"}
        ):
            suppressed += 1
            continue
        kept.append(mutation)
    return kept, suppressed


def _page_projection_is_exact_echo(
    page_mutation: Mutation,
    *,
    mutations: Sequence[Mutation],
    governed_keys: Collection[tuple[str, str, str]],
) -> bool:
    specialized = [
        mutation
        for mutation in mutations
        if mutation.twin == page_mutation.twin
        and mutation.resource_id == page_mutation.resource_id
        and (
            mutation.twin,
            mutation.resource_type,
            mutation.resource_id,
        )
        in governed_keys
        and mutation.resource_type in {"database_page", "page_markdown"}
    ]
    if len(specialized) != 1:
        return False
    echo = specialized[0]
    if not isinstance(page_mutation.before, dict) or not isinstance(page_mutation.after, dict):
        return False
    if not isinstance(echo.before, dict) or not isinstance(echo.after, dict):
        return False
    page_before = cast(dict[str, JsonValue], page_mutation.before)
    page_after = cast(dict[str, JsonValue], page_mutation.after)
    echo_before = cast(dict[str, JsonValue], echo.before)
    echo_after = cast(dict[str, JsonValue], echo.after)

    if echo.resource_type == "page_markdown":
        return (
            _changed_fields(page_mutation) == {"markdown"}
            and _changed_fields(echo) == {"markdown"}
            and page_before.get("markdown") == echo_before.get("markdown")
            and page_after.get("markdown") == echo_after.get("markdown")
        )

    if _changed_fields(page_mutation) != {"properties"}:
        return False
    old_properties = page_before.get("properties")
    new_properties = page_after.get("properties")
    if not isinstance(old_properties, dict) or not isinstance(new_properties, dict):
        return False
    old_mapping = cast(dict[str, JsonValue], old_properties)
    new_mapping = cast(dict[str, JsonValue], new_properties)
    property_changes = {
        key for key in set(old_mapping) | set(new_mapping) if old_mapping.get(key) != new_mapping.get(key)
    }
    echo_changes = _changed_fields(echo)
    if len(property_changes) != 1 or len(echo_changes) != 1:
        return False
    property_name = next(iter(property_changes))
    echo_field = next(iter(echo_changes))
    expected_prefix = f"properties.{property_name}."
    return (
        echo_field.startswith(expected_prefix)
        and old_mapping.get(property_name) == echo_before.get(echo_field)
        and new_mapping.get(property_name) == echo_after.get(echo_field)
    )


def _changed_fields(mutation: Mutation) -> set[str]:
    if not isinstance(mutation.before, dict) or not isinstance(mutation.after, dict):
        return set()
    before = cast(dict[str, JsonValue], mutation.before)
    after = cast(dict[str, JsonValue], mutation.after)
    return {key for key in set(before) | set(after) if before.get(key) != after.get(key)}


_GMAIL_MESSAGE_CANONICALIZERS = frozenset(
    {
        "gmail_messages_labels_threads_v1",
        "gmail_messages_stable",
        "gmail_messages_threads_v1",
    }
)


def _materialize_assertion_state_proofs(
    *,
    before: Sequence[CanonicalResource],
    after: Sequence[CanonicalResource],
    final: TrustedStateSnapshot,
    verification: VerificationSpec,
) -> tuple[CanonicalResource, ...]:
    """Attach relative safety and trusted-principal proofs requested by legacy verifiers."""

    fields_by_key = {
        (resource.provider_role, resource.resource_type, resource.resource_id): dict(resource.fields)
        for resource in after
    }
    resources_by_key = {
        (resource.provider_role, resource.resource_type, resource.resource_id): resource for resource in after
    }

    safety_fields = {
        "deleted_count",
        "forwarded_count",
        "sent_count",
        "unread_removed_count",
    }
    gmail_roles = {
        assertion.provider_role
        for assertion in verification.deterministic.state_assertions
        if assertion.resource_type == "provider_state" and safety_fields & set(assertion.expected)
    }
    complete_gmail_roles = {
        query.provider_role
        for query in verification.deterministic.snapshot_queries
        if query.canonicalizer in _GMAIL_MESSAGE_CANONICALIZERS
    }
    for role in sorted(gmail_roles & complete_gmail_roles):
        proof = _gmail_relative_safety_counts(before=before, after=after, provider_role=role)
        for key, resource in resources_by_key.items():
            if (
                resource.provider_role != role
                or resource.resource_type != "provider_state"
                or resource.fields.get("scope") != "all"
            ):
                continue
            for field_name, value in proof.items():
                existing = fields_by_key[key].get(field_name, _MISSING)
                if existing is not _MISSING and existing != value:
                    raise StateEvidenceError(
                        f"relative Gmail safety proof {field_name!r} conflicts on canonical resource {key!r}"
                    )
                fields_by_key[key][field_name] = value

    principal_roles = {
        assertion.provider_role
        for assertion in verification.deterministic.state_assertions
        if assertion.expected.get("created_by") == "benchmark_user"
    }
    principal_values = _trusted_principal_values(final)
    for key, resource in resources_by_key.items():
        if resource.provider_role not in principal_roles:
            continue
        created_by = resource.fields.get("created_by")
        if not isinstance(created_by, str) or created_by not in principal_values.get(
            resource.provider_role, frozenset()
        ):
            continue
        fields_by_key[key]["created_by_principal_aliases"] = ["benchmark_user"]

    return tuple(
        CanonicalResource(
            provider_role=resource.provider_role,
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            fields=fields_by_key[key],
        )
        for key, resource in sorted(resources_by_key.items())
    )


def _gmail_relative_safety_counts(
    *,
    before: Sequence[CanonicalResource],
    after: Sequence[CanonicalResource],
    provider_role: str,
) -> dict[str, int]:
    baseline = {
        resource.resource_id: resource
        for resource in before
        if resource.provider_role == provider_role and resource.resource_type == "message"
    }
    final = {
        resource.resource_id: resource
        for resource in after
        if resource.provider_role == provider_role and resource.resource_type == "message"
    }

    def has_label(resource: CanonicalResource, label: str) -> bool:
        labels = resource.fields.get("labelIds")
        return isinstance(labels, list) and label in cast(list[object], labels)

    newly_sent = [
        resource
        for resource_id, resource in final.items()
        if has_label(resource, "SENT") and (resource_id not in baseline or not has_label(baseline[resource_id], "SENT"))
    ]
    forwarded = [
        resource
        for resource in newly_sent
        if isinstance((subject := resource.fields.get("subject")), str)
        and re.match(r"^\s*(?:fwd?|forwarded)\s*:", subject, re.IGNORECASE) is not None
    ]
    unread_removed = sum(
        has_label(resource, "UNREAD") and resource_id in final and not has_label(final[resource_id], "UNREAD")
        for resource_id, resource in baseline.items()
    )
    return {
        "sent_count": len(newly_sent),
        "deleted_count": len(set(baseline) - set(final)),
        "forwarded_count": len(forwarded),
        "unread_removed_count": unread_removed,
    }


def _trusted_principal_values(snapshot: TrustedStateSnapshot) -> dict[str, frozenset[str]]:
    by_role: dict[str, set[str]] = {}
    ambiguous_roles: set[str] = set()
    for provider in snapshot.providers.values():
        users = provider.state.get("users")
        if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], dict):
            continue
        if provider.provider_role in by_role:
            ambiguous_roles.add(provider.provider_role)
            continue
        user = cast(dict[str, JsonValue], users[0])
        values = {
            value
            for key in ("accountId", "account_id", "displayName", "display_name", "email", "id")
            if isinstance((value := user.get(key)), str) and value
        }
        if values:
            by_role[provider.provider_role] = values
    return {role: frozenset(values) for role, values in by_role.items() if role not in ambiguous_roles}


def _materialize_selector_proofs(
    *,
    before: Sequence[CanonicalResource],
    after: Sequence[CanonicalResource],
    assertions: Sequence[StateAssertionSpec],
) -> tuple[CanonicalResource, ...]:
    baseline_keys = {(resource.provider_role, resource.resource_type, resource.resource_id) for resource in before}
    fields_by_key = {
        (resource.provider_role, resource.resource_type, resource.resource_id): dict(resource.fields)
        for resource in after
    }
    resources_by_key = {
        (resource.provider_role, resource.resource_type, resource.resource_id): resource for resource in after
    }
    for assertion in assertions:
        # This cross-resource selector is resolved by baseline_semantics, which
        # has the sibling assertion context needed to identify the target.
        if "exclude_exact_target" in assertion.selector:
            continue
        for key, resource in resources_by_key.items():
            if resource.provider_role != assertion.provider_role:
                continue
            if resource.resource_type != assertion.resource_type:
                continue
            document = resource.match_document()
            if not _selector_matches(
                assertion.selector,
                document,
                existed_at_baseline=key in baseline_keys,
            ):
                continue
            for selector_key, expected in assertion.selector.items():
                if not _selector_operator(selector_key):
                    continue
                existing = fields_by_key[key].get(selector_key, _MISSING)
                if existing is not _MISSING and existing != expected:
                    raise StateEvidenceError(f"selector proof {selector_key!r} conflicts on canonical resource {key!r}")
                fields_by_key[key][selector_key] = expected
    return tuple(
        CanonicalResource(
            provider_role=resource.provider_role,
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            fields=fields_by_key[key],
        )
        for key, resource in sorted(resources_by_key.items())
    )


def _materialize_relational_proofs(
    *,
    before: Sequence[CanonicalResource],
    after: Sequence[CanonicalResource],
    assertions: Sequence[StateAssertionSpec],
) -> tuple[CanonicalResource, ...]:
    fields_by_key = {
        (resource.provider_role, resource.resource_type, resource.resource_id): dict(resource.fields)
        for resource in after
    }
    resources_by_key = {
        (resource.provider_role, resource.resource_type, resource.resource_id): resource for resource in after
    }
    assertions_by_id = {assertion.id: assertion for assertion in assertions}

    def selected(assertion: StateAssertionSpec) -> list[CanonicalResource]:
        return [
            resource
            for resource in resources_by_key.values()
            if resource.provider_role == assertion.provider_role
            and resource.resource_type == assertion.resource_type
            and _literal_subset(assertion.selector, resource.match_document())
        ]

    for assertion in assertions:
        targets = selected(assertion)
        for target in targets:
            key = (target.provider_role, target.resource_type, target.resource_id)
            if assertion.expected.get("present") is True:
                fields_by_key[key]["present"] = True
            for field_name, expected in assertion.expected.items():
                if field_name in _HELPER_EXPECTED_KEYS or field_name in {"except", "present"}:
                    continue
                if not isinstance(expected, str):
                    continue
                baseline_proof = _prove_baseline_reference(
                    target=target,
                    field_name=field_name,
                    reference=expected,
                    before=before,
                )
                if baseline_proof is True:
                    fields_by_key[key][field_name] = expected
                    continue
                if baseline_proof is False:
                    # The reference was understood but the candidate state did
                    # not satisfy it. Preserve the actual value so the state
                    # assertion fails normally rather than invalidating grading.
                    continue
                if field_name == "attached_to" and expected in assertions_by_id:
                    references = selected(assertions_by_id[expected])
                    if not references:
                        # A missing referenced resource is candidate state, not
                        # a malformed verifier contract. Leave the provider
                        # value intact so the assertion fails deterministically.
                        continue
                    if len(references) > 1:
                        raise StateEvidenceError(
                            f"assertion {assertion.id!r} cannot resolve {field_name!r} reference {expected!r} uniquely"
                        )
                    provider_id = _provider_id(references[0].resource_id)
                    actual = fields_by_key[key].get(field_name)
                    if str(actual) == provider_id:
                        fields_by_key[key][field_name] = expected
                    continue
                if field_name == "tracker_identifier_matches":
                    match = _ASSERTION_REFERENCE.fullmatch(expected)
                    if match is None or match.group("assertion") not in assertions_by_id:
                        raise StateEvidenceError(f"assertion {assertion.id!r} has unsupported relation {expected!r}")
                    references = selected(assertions_by_id[match.group("assertion")])
                    if not references:
                        # Missing candidate state should produce a failed
                        # relation assertion, while ambiguity remains a grader
                        # validity error below.
                        continue
                    if len(references) > 1:
                        raise StateEvidenceError(
                            f"assertion {assertion.id!r} cannot resolve relation {expected!r} uniquely"
                        )
                    referenced_value = references[0].fields.get(match.group("field"))
                    text = next(
                        (
                            value
                            for candidate in ("text", "content")
                            if isinstance(
                                (value := fields_by_key[key].get(candidate)),
                                str,
                            )
                        ),
                        None,
                    )
                    if isinstance(referenced_value, str) and isinstance(text, str) and referenced_value in text:
                        fields_by_key[key][field_name] = expected
                    continue
                if expected.startswith("baseline.") or expected.startswith("sa_"):
                    raise StateEvidenceError(f"assertion {assertion.id!r} uses unsupported symbolic value {expected!r}")
    return tuple(
        CanonicalResource(
            provider_role=resource.provider_role,
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            fields=fields_by_key[key],
        )
        for key, resource in sorted(resources_by_key.items())
    )


def _prove_baseline_reference(
    *,
    target: CanonicalResource,
    field_name: str,
    reference: str,
    before: Sequence[CanonicalResource],
) -> bool | None:
    pull_match = _BASELINE_PULL_REFERENCE.fullmatch(reference)
    merge_request_match = _BASELINE_MERGE_REQUEST_REFERENCE.fullmatch(reference)
    if pull_match is None and merge_request_match is None:
        return None
    if pull_match is not None:
        number = int(pull_match.group(1))
        candidates = [
            resource
            for resource in before
            if resource.provider_role == target.provider_role
            and resource.resource_type == "pull_request"
            and resource.fields.get("repository") == target.fields.get("repository")
            and resource.fields.get("number") == number
        ]
        baseline_field = "head_sha"
    else:
        assert merge_request_match is not None
        number = int(merge_request_match.group(1))
        candidates = [
            resource
            for resource in before
            if resource.provider_role == target.provider_role
            and resource.resource_type == "merge_request"
            and resource.fields.get("project") == target.fields.get("project")
            and resource.fields.get("iid") == number
        ]
        baseline_field = "sha"
    if len(candidates) != 1:
        raise StateEvidenceError(f"symbolic baseline reference {reference!r} does not resolve uniquely")
    target_value = target.fields.get(field_name, _MISSING)
    baseline_value = candidates[0].fields.get(baseline_field, _MISSING)
    if not (
        isinstance(target_value, str)
        and target_value.strip()
        and isinstance(baseline_value, str)
        and baseline_value.strip()
    ):
        raise StateEvidenceError(
            f"symbolic baseline reference {reference!r} requires non-empty string "
            f"{field_name!r} and {baseline_field!r} fields"
        )
    return target_value == baseline_value


def _provider_id(resource_id: str) -> str:
    for marker in (":review:", ":note:", ":comment:", ":discussion:"):
        if marker in resource_id:
            suffix = resource_id.rsplit(marker, 1)[1]
            return suffix.split(":", 1)[0]
    return resource_id


def _selector_matches(
    selector: Mapping[str, Any],
    document: Mapping[str, Any],
    *,
    existed_at_baseline: bool,
) -> bool:
    for key, expected in selector.items():
        if key == "baseline_only":
            if not isinstance(expected, bool):
                raise StateEvidenceError("baseline_only selector must be boolean")
            if expected != existed_at_baseline:
                return False
            continue
        if key == "exclude_exact_target":
            raise StateEvidenceError(
                "exclude_exact_target is unsupported because it does not identify the excluded resource"
            )
        if key == "exclude":
            if not isinstance(expected, dict):
                raise StateEvidenceError("exclude selector must be an object")
            if _selector_matches(
                cast(dict[str, Any], expected),
                document,
                existed_at_baseline=existed_at_baseline,
            ):
                return False
            continue
        if key == "number_lte":
            actual = document.get("number")
            if (
                not isinstance(actual, int | float)
                or isinstance(actual, bool)
                or not isinstance(expected, int | float)
                or isinstance(expected, bool)
                or actual > expected
            ):
                return False
            continue
        if key == "exclude_number":
            if document.get("number") == expected:
                return False
            continue
        if key.endswith("_in"):
            if not isinstance(expected, list):
                raise StateEvidenceError(f"{key} selector must be a list")
            if document.get(key.removesuffix("_in")) not in expected:
                return False
            continue
        if key.endswith("_contains"):
            actual = document.get(key.removesuffix("_contains"))
            if not isinstance(actual, str) or not isinstance(expected, str) or expected not in actual:
                return False
            continue
        if key.endswith("_pattern"):
            actual = document.get(key.removesuffix("_pattern"))
            if not isinstance(actual, str) or not isinstance(expected, str):
                return False
            try:
                if re.fullmatch(expected, actual) is None:
                    return False
            except re.error as error:
                raise StateEvidenceError(f"{key} selector has invalid regular expression: {error}") from error
            continue
        actual = document.get(key, _MISSING)
        if actual is _MISSING or not state_fact_matches(key, expected, actual):
            return False
    return True


def _selector_operator(key: str) -> bool:
    return key in {
        "baseline_only",
        "exclude",
        "exclude_exact_target",
        "exclude_number",
        "number_lte",
    } or key.endswith(("_contains", "_in", "_pattern"))


def _literal_subset(selector: Mapping[str, Any], document: Mapping[str, Any]) -> bool:
    return state_document_matches(selector, document)


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


def _resource_index(
    resources: Sequence[CanonicalResource],
) -> dict[tuple[str, str, str], CanonicalResource]:
    index: dict[tuple[str, str, str], CanonicalResource] = {}
    for resource in resources:
        key = (resource.provider_role, resource.resource_type, resource.resource_id)
        if key in index:
            raise StateEvidenceError(f"duplicate canonical resource identity {key!r}")
        index[key] = resource
    return index


_MISSING = object()


__all__ = [
    "CanonicalizerCoverage",
    "DeterministicStateEvidence",
    "STANDARD_CANONICALIZER_COVERAGE",
    "StateEvidenceError",
    "build_deterministic_state_evidence",
]
