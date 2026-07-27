from __future__ import annotations

from typing import Any

import pytest

from arga_twins_benchmark.evaluation.admin_delta_claims import (
    AdminDeltaClaimError,
    claim_provider_admin_deltas,
)
from arga_twins_benchmark.evaluation.protocol import Mutation
from arga_twins_benchmark.evaluation.state_capture import RawStateDelta


def _admin_delta(
    provider_name: str,
    provider_role: str,
    path: tuple[str, ...],
    operation: str,
    *,
    before: Any = None,
    after: Any = None,
) -> RawStateDelta:
    return RawStateDelta(
        provider_name=provider_name,
        provider_role=provider_role,
        scope="admin",
        path=path,
        operation=operation,
        before=before,
        after=after,
    )


def _calendar_attendee_bundle(
    *,
    raw_email: str = "alex@acme.example",
    canonical_email: str = "alex@acme.example",
) -> tuple[list[RawStateDelta], list[Mutation]]:
    event_id = "evt00001"
    calendar_id = "calendar-1@example.test"
    deltas = [
        _admin_delta(
            "google_calendar",
            "calendar",
            ("events", f"id={event_id}", "attendees", f"email={raw_email}"),
            "create",
            after={"email": raw_email},
        ),
        _admin_delta(
            "google_calendar",
            "calendar",
            ("events", f"id={event_id}", "_sync_version"),
            "update",
            before=2,
            after=8,
        ),
        _admin_delta(
            "google_calendar",
            "calendar",
            ("events", f"id={event_id}", "etag"),
            "update",
            before='"old"',
            after='"new"',
        ),
        _admin_delta(
            "google_calendar",
            "calendar",
            ("events", f"id={event_id}", "updated"),
            "update",
            before="2026-01-15T00:02:28+00:00",
            after="2026-01-15T00:09:52+00:00",
        ),
        _admin_delta(
            "google_calendar",
            "calendar",
            ("meta", "sync_version"),
            "update",
            before=7,
            after=8,
        ),
        _admin_delta(
            "google_calendar",
            "calendar",
            ("notifications", "id=notification-1"),
            "create",
            after={
                "action": "updated",
                "calendar_id": calendar_id,
                "producer": "googlecalendar",
                "envelope": {
                    "calendar_id": calendar_id,
                    "resource": "event",
                    "resource_id": event_id,
                },
            },
        ),
    ]
    mutations = [
        Mutation(
            twin="calendar",
            resource_type="event",
            resource_id=event_id,
            operation="update",
            before={"attendees": [], "attendee_emails": []},
            after={
                "attendees": [
                    {
                        "email": canonical_email,
                        "responseStatus": None,
                        "optional": False,
                    }
                ],
                "attendee_emails": [canonical_email],
            },
        )
    ]
    return deltas, mutations


def test_calendar_claims_identity_aware_attendee_and_exact_metadata_echoes() -> None:
    deltas, mutations = _calendar_attendee_bundle()

    claims = claim_provider_admin_deltas(
        raw_deltas=deltas,
        canonical_mutations=mutations,
    )

    assert claims.claimed_indices == frozenset(range(len(deltas)))
    assert claims.synthetic_mutations == ()


def test_calendar_rejects_attendee_identity_not_in_canonical_event() -> None:
    deltas, mutations = _calendar_attendee_bundle(raw_email="mallory@example.test")

    with pytest.raises(
        AdminDeltaClaimError,
        match="attendee identity delta differs from the canonical event mutation",
    ):
        claim_provider_admin_deltas(
            raw_deltas=deltas,
            canonical_mutations=mutations,
        )


def _notion_event_delta(
    *,
    event_type: str,
    source_method: str,
    entity: dict[str, Any],
) -> RawStateDelta:
    event_id = "event-1"
    return _admin_delta(
        "notion",
        "knowledge_base",
        ("events", f"id={event_id}"),
        "create",
        after={
            "id": event_id,
            "type": event_type,
            "source_method": source_method,
            "pending": True,
            "payload": {
                "id": event_id,
                "type": event_type,
                "entity": entity,
            },
        },
    )


def test_notion_page_created_claims_same_page_canonical_create() -> None:
    page_id = "page-1"
    delta = _notion_event_delta(
        event_type="page.created",
        source_method="pages.create",
        entity={"object": "page", "id": page_id},
    )
    mutation = Mutation(
        twin="knowledge_base",
        resource_type="page",
        resource_id=page_id,
        operation="create",
        after={"title": "Created page"},
    )

    claims = claim_provider_admin_deltas(
        raw_deltas=[delta],
        canonical_mutations=[mutation],
    )

    assert claims.claimed_indices == frozenset({0})


def test_notion_page_created_rejects_different_canonical_page() -> None:
    delta = _notion_event_delta(
        event_type="page.created",
        source_method="pages.create",
        entity={"object": "page", "id": "page-1"},
    )
    mutation = Mutation(
        twin="knowledge_base",
        resource_type="page",
        resource_id="page-2",
        operation="create",
        after={"title": "Different page"},
    )

    with pytest.raises(AdminDeltaClaimError, match="no canonical page mutation"):
        claim_provider_admin_deltas(
            raw_deltas=[delta],
            canonical_mutations=[mutation],
        )


def test_notion_block_update_claims_same_page_markdown_update() -> None:
    page_id = "page-1"
    delta = _notion_event_delta(
        event_type="page.content_updated",
        source_method="blocks.update",
        entity={
            "object": "block",
            "id": "block-1",
            "parent": {"type": "page_id", "page_id": page_id},
        },
    )
    mutation = Mutation(
        twin="knowledge_base",
        resource_type="page_markdown",
        resource_id=page_id,
        operation="update",
        before={"markdown": "Before"},
        after={"markdown": "After"},
    )

    claims = claim_provider_admin_deltas(
        raw_deltas=[delta],
        canonical_mutations=[mutation],
    )

    assert claims.claimed_indices == frozenset({0})


@pytest.mark.parametrize(
    "mutation",
    [
        Mutation(
            twin="knowledge_base",
            resource_type="page_markdown",
            resource_id="different-page",
            operation="update",
        ),
        Mutation(
            twin="knowledge_base",
            resource_type="page_markdown",
            resource_id="page-1",
            operation="create",
        ),
    ],
)
def test_notion_block_update_rejects_wrong_page_or_operation(
    mutation: Mutation,
) -> None:
    delta = _notion_event_delta(
        event_type="page.content_updated",
        source_method="blocks.update",
        entity={
            "object": "block",
            "id": "block-1",
            "parent": {"type": "page_id", "page_id": "page-1"},
        },
    )

    with pytest.raises(AdminDeltaClaimError, match="no canonical page mutation"):
        claim_provider_admin_deltas(
            raw_deltas=[delta],
            canonical_mutations=[mutation],
        )


def test_stripe_claims_empty_lazy_generic_collection_as_internal_bookkeeping() -> None:
    deltas = [
        _admin_delta(
            "stripe",
            "payments",
            ("counts", "generic_resources"),
            "update",
            before=0,
            after=1,
        ),
        _admin_delta(
            "stripe",
            "payments",
            ("generic_resources", "/v1/entitlements/features"),
            "create",
            after={},
        ),
    ]

    claims = claim_provider_admin_deltas(
        raw_deltas=deltas,
        canonical_mutations=[],
    )

    assert claims.claimed_indices == frozenset({0, 1})
    assert claims.synthetic_mutations == ()


@pytest.mark.parametrize(
    "deltas",
    [
        [
            _admin_delta(
                "stripe",
                "payments",
                ("generic_resources", "/v1/entitlements/features"),
                "create",
                after={"feature": "created"},
            ),
            _admin_delta(
                "stripe",
                "payments",
                ("counts", "generic_resources"),
                "update",
                before=0,
                after=1,
            ),
        ],
        [
            _admin_delta(
                "stripe",
                "payments",
                ("generic_resources", "/v1/entitlements/features"),
                "create",
                after={},
            )
        ],
    ],
)
def test_stripe_rejects_nonempty_or_unpaired_generic_materialization(
    deltas: list[RawStateDelta],
) -> None:
    with pytest.raises(
        AdminDeltaClaimError,
        match="generic resource materialization is inconsistent",
    ):
        claim_provider_admin_deltas(
            raw_deltas=deltas,
            canonical_mutations=[],
        )
