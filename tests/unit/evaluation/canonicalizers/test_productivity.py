from __future__ import annotations

import base64

import pytest

from arga_twins_benchmark.evaluation.canonicalizers.productivity import (
    PRODUCTIVITY_CANONICALIZERS,
    drive_files_content_hash_v1,
    gmail_drafts_v1,
    gmail_messages_labels_threads_v1,
    google_calendar_events_v1,
    google_calendar_state_stable,
    google_drive_state_stable,
    notion_pages_markdown_v1,
    notion_state_stable,
    stripe_prices_stable,
)
from arga_twins_benchmark.evaluation.deterministic import CanonicalResource
from arga_twins_benchmark.evaluation.state_capture import CapturedQueryState, StateCaptureError


def capture(
    body: object,
    *,
    canonicalizer: str,
    role: str,
    path: str,
) -> CapturedQueryState:
    return CapturedQueryState(
        query_id="snapshot",
        provider_name="provider",
        provider_role=role,
        method="GET",
        path=path,
        canonicalizer=canonicalizer,
        status_code=200,
        body=body,  # type: ignore[arg-type]
    )


def resource(
    resources: list[CanonicalResource] | tuple[CanonicalResource, ...],
    resource_type: str,
    resource_id: str,
) -> CanonicalResource:
    return next(item for item in resources if item.resource_type == resource_type and item.resource_id == resource_id)


def encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def test_registry_covers_every_productivity_fixture_name() -> None:
    assert set(PRODUCTIVITY_CANONICALIZERS) == {
        "drive_files_content_hash_v1",
        "gmail_drafts_stable",
        "gmail_drafts_v1",
        "gmail_labels_stable",
        "gmail_labels_v1",
        "gmail_messages_labels_threads_v1",
        "gmail_messages_stable",
        "gmail_messages_threads_v1",
        "gmail_threads_stable",
        "google_calendar_all_events_v1",
        "google_calendar_events_v1",
        "google_calendar_list_v1",
        "google_calendar_state_stable",
        "google_drive_state_stable",
        "notion_pages_complete",
        "notion_pages_markdown_v1",
        "notion_spec_pages_stable",
        "notion_state_stable",
        "stripe_customers_stable",
        "stripe_prices_stable",
        "stripe_products_stable",
    }


def test_notion_admin_projects_database_properties_and_ignores_times() -> None:
    page = {
        "id": "page-pol-3",
        "type": "database",
        "title": "POL-3",
        "created_at": "2030-05-14T00:00:00Z",
        "updated_at": "2030-05-14T00:01:00Z",
        "metadata": {
            "properties": {
                "Policy": {"type": "title", "title": [{"plain_text": "POL-3"}]},
                "Lifecycle": {"type": "select", "select": {"name": "Current"}},
                "Version": {"type": "rich_text", "rich_text": [{"plain_text": "3.0"}]},
                "Supersedes": {"type": "rich_text", "rich_text": [{"plain_text": "POL-2"}]},
                "Artifact Digest": {
                    "type": "rich_text",
                    "rich_text": [{"plain_text": "sha256:pol3-7f31"}],
                },
            }
        },
    }
    first = list(
        notion_state_stable(
            capture(
                {"pages": [page], "logical_now": "2030-05-14T00:02:00Z"},
                canonicalizer="notion_state_stable",
                role="knowledge_base",
                path="/admin/state",
            )
        )
    )
    page["updated_at"] = "2031-01-01T00:00:00Z"
    second = list(
        notion_state_stable(
            capture(
                {"pages": [page], "logical_now": "2031-01-01T00:00:00Z"},
                canonicalizer="notion_state_stable",
                role="knowledge_base",
                path="/admin/state",
            )
        )
    )
    assert first == second
    database_page = resource(first, "database_page", "page-pol-3")
    assert database_page.fields["database"] == "Policy Registry"
    assert database_page.fields["Lifecycle"] == "Current"
    assert database_page.fields["Artifact_Digest"] == "sha256:pol3-7f31"
    assert database_page.fields["properties.Lifecycle.select.name"] == "Current"


def test_notion_markdown_is_only_emitted_when_provider_supplies_it() -> None:
    resources = list(
        notion_pages_markdown_v1(
            capture(
                {
                    "results": [
                        {
                            "object": "page",
                            "id": "page-rb-77",
                            "title": "[RB-77] Payments failover",
                            "markdown": "# Payments failover\n",
                        }
                    ],
                    "has_more": False,
                    "next_cursor": None,
                },
                canonicalizer="notion_pages_markdown_v1",
                role="knowledge_base",
                path="/v1/search",
            )
        )
    )
    assert resource(resources, "page_markdown", "page-rb-77").fields == {
        "title": "[RB-77] Payments failover",
        "markdown": "# Payments failover\n",
    }


def test_drive_admin_projects_permissions_and_content_marker() -> None:
    resources = list(
        google_drive_state_stable(
            capture(
                {
                    "logical_now": "2030-05-14T00:00:00Z",
                    "files": [
                        {
                            "id": "file-1",
                            "name": "Q2 SOC2 Evidence.txt",
                            "mimeType": "text/plain",
                            "description": "Content Marker: SOC2-Q2-311",
                            "md5Checksum": "abc",
                            "parents": ["folder-1"],
                            "permissions": [
                                {
                                    "id": "permission-1",
                                    "type": "user",
                                    "role": "reader",
                                    "emailAddress": "auditor@trusted.example",
                                }
                            ],
                        }
                    ],
                },
                canonicalizer="google_drive_state_stable",
                role="storage",
                path="/admin/state",
            )
        )
    )
    permission = resource(resources, "file_permission", "file-1:permission-1")
    assert permission.fields["content_marker"] == "SOC2-Q2-311"
    assert permission.fields["emailAddress"] == "auditor@trusted.example"


def test_drive_list_fails_closed_on_incomplete_pagination() -> None:
    with pytest.raises(StateCaptureError, match="incomplete"):
        drive_files_content_hash_v1(
            capture(
                {"files": [], "nextPageToken": "next", "incompleteSearch": False},
                canonicalizer="drive_files_content_hash_v1",
                role="file_storage",
                path="/drive/v3/files",
            )
        )


def test_gmail_admin_projects_named_labels_invoice_and_draft() -> None:
    message = {
        "id": "msg-1",
        "threadId": "thr_inv_7301",
        "labelIds": ["INBOX", "UNREAD", "Label_1"],
        "payload": {
            "headers": [
                {"name": "From", "value": "billing@northstar.example"},
                {"name": "To", "value": "owner@gmail-twin.local"},
                {"name": "Subject", "value": "Invoice INV-7301"},
            ],
            "body": {"data": encoded("Invoice ID: INV-7301")},
        },
    }
    draft_message = {
        "id": "draft-message-1",
        "threadId": "thr_inv_7301",
        "labelIds": ["DRAFT"],
        "payload": {
            "headers": [
                {"name": "To", "value": "billing@northstar.example"},
                {"name": "Subject", "value": "Re: Invoice INV-7301"},
            ],
            "body": {"data": encoded("Received INV-7301; finance review requested.")},
        },
    }
    body = {
        "mailboxes": {
            "owner@gmail-twin.local": {
                "messages": [message],
                "drafts": [{"id": "draft-1", "message": draft_message}],
                "labels": [{"id": "Label_1", "name": "Needs-Finance", "type": "user"}],
            }
        }
    }
    messages = list(
        gmail_messages_labels_threads_v1(
            capture(
                body,
                canonicalizer="gmail_messages_labels_threads_v1",
                role="email",
                path="/inspect",
            )
        )
    )
    message_resource = resource(messages, "message", "msg-1")
    assert message_resource.fields["invoice_id"] == "INV-7301"
    assert message_resource.fields["labels_contain"] == ["INBOX", "Needs-Finance", "UNREAD"]

    drafts = list(
        gmail_drafts_v1(
            capture(body, canonicalizer="gmail_drafts_v1", role="email", path="/inspect")
        )
    )
    assert resource(drafts, "draft", "draft-1").fields["body"] == (
        "Received INV-7301; finance review requested."
    )


def test_calendar_event_projection_handles_list_and_admin_shapes() -> None:
    event = {
        "id": "event-1",
        "summary": "Launch review",
        "start": {"dateTime": "2030-05-16T08:00:00-07:00"},
        "end": {"dateTime": "2030-05-16T08:45:00-07:00"},
        "attendees": [{"email": "alex@acme.example", "responseStatus": "needsAction"}],
    }
    listed = list(
        google_calendar_events_v1(
            capture(
                {"items": [event], "summary": "Work", "nextPageToken": None},
                canonicalizer="google_calendar_events_v1",
                role="calendar",
                path="/calendar/v3/calendars/calendar-1@example.test/events",
            )
        )
    )
    listed_event = resource(listed, "event", "event-1")
    assert listed_event.fields["calendar"] == "Work"
    assert listed_event.fields["start"] == "2030-05-16T15:00:00Z"
    assert listed_event.fields["attendee_emails"] == ["alex@acme.example"]

    event["_calendar_id"] = "calendar-1@example.test"
    admin = list(
        google_calendar_state_stable(
            capture(
                {
                    "calendars": [{"id": "calendar-1@example.test", "summary": "Work"}],
                    "events": [event],
                },
                canonicalizer="google_calendar_state_stable",
                role="calendar",
                path="/_admin/state",
            )
        )
    )
    assert resource(admin, "event_collection", "Work").fields["event_count"] == 1


def test_stripe_price_uses_expanded_product_and_strips_created_time() -> None:
    price = {
        "id": "price-1",
        "product": {"id": "prod-1", "name": "Pro Monthly"},
        "unit_amount": 7900,
        "currency": "usd",
        "nickname": "pro-monthly-usd-79",
        "lookup_key": "pro_monthly_usd_7900",
        "created": 123,
    }
    resources = list(
        stripe_prices_stable(
            capture(
                {"data": [price], "has_more": False},
                canonicalizer="stripe_prices_stable",
                role="payments",
                path="/v1/prices?limit=100",
            )
        )
    )
    price_resource = resource(resources, "price", "price-1")
    assert price_resource.fields["product_name"] == "Pro Monthly"
    assert "created" not in price_resource.fields
