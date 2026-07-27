from __future__ import annotations

from typing import Any, cast

import pytest

from arga_twins_benchmark.evaluation.snapshot_enrichment import (
    enrich_snapshot_from_trusted_state,
)
from arga_twins_benchmark.evaluation.state_capture import (
    CapturedProviderState,
    CapturedQueryState,
    StateCaptureError,
    TrustedStateSnapshot,
)


def _query(
    query_id: str,
    *,
    provider: str,
    role: str,
    canonicalizer: str,
    path: str,
    body: Any,
) -> CapturedQueryState:
    return CapturedQueryState(
        query_id=query_id,
        provider_name=provider,
        provider_role=role,
        method="GET",
        path=path,
        canonicalizer=canonicalizer,
        status_code=200,
        body=body,
    )


def _snapshot(
    *,
    provider: str,
    role: str,
    state: dict[str, Any],
    queries: list[CapturedQueryState],
) -> TrustedStateSnapshot:
    return TrustedStateSnapshot(
        providers={
            provider: CapturedProviderState(
                provider_name=provider,
                provider_role=role,
                state=state,
            )
        },
        queries={query.query_id: query for query in queries},
    )


def test_slack_enrichment_joins_exact_channels_and_message_events() -> None:
    capture = _query(
        "channels",
        provider="slack",
        role="team_chat",
        canonicalizer="slack_channels_messages_stable",
        path="/api/conversations.list",
        body={
            "ok": True,
            "channels": [
                {"id": "C1", "name": "incidents"},
                {"id": "C2", "name": "general"},
            ],
        },
    )
    snapshot = _snapshot(
        provider="slack",
        role="team_chat",
        state={
            "channels": [
                {"id": "C1", "name": "incidents", "message_count": 2},
                {"id": "C2", "name": "general", "message_count": 0},
            ],
            "events": [
                {
                    "envelope": {
                        "event": {
                            "type": "message",
                            "channel": "C1",
                            "ts": "2.000001",
                            "text": "Second",
                            "user": "U1",
                        }
                    }
                },
                {"envelope": {"event": {"type": "reaction_added"}}},
                {
                    "envelope": {
                        "event": {
                            "type": "message",
                            "channel": "C1",
                            "ts": "1.000001",
                            "text": "First",
                            "user": "U2",
                        }
                    }
                },
            ],
        },
        queries=[capture],
    )

    enriched = enrich_snapshot_from_trusted_state(snapshot)

    assert isinstance(capture.body, dict)
    assert "messages_by_channel" not in capture.body
    body = enriched.queries["channels"].body
    assert isinstance(body, dict)
    messages = body["messages_by_channel"]
    assert isinstance(messages, dict)
    channel_messages = messages["C1"]
    assert isinstance(channel_messages, list)
    assert all(isinstance(message, dict) for message in channel_messages)
    typed_messages = cast(list[dict[str, Any]], channel_messages)
    assert [message["ts"] for message in typed_messages] == ["1.000001", "2.000001"]
    assert messages["C2"] == []


@pytest.mark.parametrize(
    ("channels", "events", "error"),
    [
        (
            [{"id": "OTHER", "name": "other", "message_count": 0}],
            [],
            "different channel IDs",
        ),
        (
            [{"id": "C1", "name": "incidents", "message_count": 2}],
            [
                {
                    "envelope": {
                        "event": {
                            "type": "message",
                            "channel": "C1",
                            "ts": "1.000001",
                            "text": "Only one",
                            "user": "U1",
                        }
                    }
                }
            ],
            "message_count differs",
        ),
        (
            [{"id": "C1", "name": "incidents", "message_count": 0}],
            [
                {
                    "envelope": {
                        "event": {
                            "type": "message",
                            "channel": "OTHER",
                            "ts": "1.000001",
                            "text": "Outside scope",
                            "user": "U1",
                        }
                    }
                }
            ],
            "outside conversations.list",
        ),
    ],
)
def test_slack_enrichment_rejects_inconsistent_trusted_joins(
    channels: list[dict[str, Any]],
    events: list[dict[str, Any]],
    error: str,
) -> None:
    capture = _query(
        "channels",
        provider="slack",
        role="team_chat",
        canonicalizer="slack_channels_messages_v1",
        path="/api/conversations.list",
        body={"ok": True, "channels": [{"id": "C1", "name": "incidents"}]},
    )
    snapshot = _snapshot(
        provider="slack",
        role="team_chat",
        state={"channels": channels, "events": events},
        queries=[capture],
    )

    with pytest.raises(StateCaptureError, match=error):
        enrich_snapshot_from_trusted_state(snapshot)


def test_gmail_enrichment_uses_full_trusted_mailbox_after_exact_id_join() -> None:
    capture = _query(
        "messages",
        provider="gmail",
        role="email",
        canonicalizer="gmail_messages_labels_threads_v1",
        path="/gmail/v1/users/me/messages",
        body={"messages": [{"id": "m1"}, {"id": "m2"}]},
    )
    mailboxes = {
        "ops@example.test": {
            "messages": [
                {"id": "m1", "threadId": "t1", "snippet": "Trusted first"},
                {"id": "m2", "threadId": "t2", "snippet": "Trusted second"},
            ]
        }
    }
    snapshot = _snapshot(
        provider="gmail",
        role="email",
        state={"mailboxes": mailboxes},
        queries=[capture],
    )

    enriched = enrich_snapshot_from_trusted_state(snapshot)

    assert enriched.queries["messages"].body == {"mailboxes": mailboxes}
    assert capture.body == {"messages": [{"id": "m1"}, {"id": "m2"}]}


def test_gmail_enrichment_rejects_query_and_mailbox_id_mismatch() -> None:
    capture = _query(
        "drafts",
        provider="gmail",
        role="email",
        canonicalizer="gmail_drafts_stable",
        path="/gmail/v1/users/me/drafts",
        body={"drafts": [{"id": "draft-query"}]},
    )
    snapshot = _snapshot(
        provider="gmail",
        role="email",
        state={"mailboxes": {"ops@example.test": {"drafts": [{"id": "draft-trusted", "message": {"id": "m1"}}]}}},
        queries=[capture],
    )

    with pytest.raises(StateCaptureError, match="different drafts IDs"):
        enrich_snapshot_from_trusted_state(snapshot)


def test_notion_enrichment_uses_trusted_page_and_only_supplements_markdown() -> None:
    capture = _query(
        "pages",
        provider="notion",
        role="knowledge_base",
        canonicalizer="notion_pages_markdown_v1",
        path="/v1/search",
        body={
            "results": [
                {
                    "object": "page",
                    "id": "page-1",
                    "properties": {"title": "Untrusted summary"},
                    "markdown": "# Captured blocks",
                },
                {"object": "database", "id": "database-result"},
            ]
        },
    )
    trusted_page = {
        "object": "page",
        "id": "page-1",
        "properties": {"title": "Trusted page"},
        "archived": False,
    }
    snapshot = _snapshot(
        provider="notion",
        role="knowledge_base",
        state={"pages": [trusted_page]},
        queries=[capture],
    )

    enriched = enrich_snapshot_from_trusted_state(snapshot)

    assert enriched.queries["pages"].body == {"pages": [{**trusted_page, "markdown": "# Captured blocks"}]}
    assert "markdown" not in trusted_page


@pytest.mark.parametrize(
    ("results", "error"),
    [
        (
            [{"object": "page", "id": "different-page"}],
            "different page IDs",
        ),
        (
            [{"object": "page", "id": "page-1", "markdown": ["not", "text"]}],
            "supplemental Markdown must be a string",
        ),
    ],
)
def test_notion_enrichment_rejects_identity_and_markdown_mismatches(
    results: list[dict[str, Any]],
    error: str,
) -> None:
    capture = _query(
        "pages",
        provider="notion",
        role="knowledge_base",
        canonicalizer="notion_pages_complete",
        path="/v1/search",
        body={"results": results},
    )
    snapshot = _snapshot(
        provider="notion",
        role="knowledge_base",
        state={"pages": [{"object": "page", "id": "page-1"}]},
        queries=[capture],
    )

    with pytest.raises(StateCaptureError, match=error):
        enrich_snapshot_from_trusted_state(snapshot)


def test_stripe_price_enrichment_joins_the_trusted_product_name() -> None:
    capture = _query(
        "prices",
        provider="stripe",
        role="payments",
        canonicalizer="stripe_prices_stable",
        path="/v1/prices?limit=100",
        body={
            "object": "list",
            "data": [
                {
                    "id": "price_target",
                    "product": "prod_target",
                    "unit_amount": 7900,
                    "currency": "usd",
                }
            ],
            "has_more": False,
        },
    )
    snapshot = _snapshot(
        provider="stripe",
        role="payments",
        state={
            "prices": {
                "price_target": {
                    "id": "price_target",
                    "product": "prod_target",
                    "unit_amount": 7900,
                    "currency": "usd",
                }
            },
            "products": {
                "prod_target": {
                    "id": "prod_target",
                    "name": "Pro Monthly",
                }
            },
        },
        queries=[capture],
    )

    enriched = enrich_snapshot_from_trusted_state(snapshot)

    assert capture.body == {
        "object": "list",
        "data": [
            {
                "id": "price_target",
                "product": "prod_target",
                "unit_amount": 7900,
                "currency": "usd",
            }
        ],
        "has_more": False,
    }
    enriched_body = enriched.queries["prices"].body
    assert isinstance(enriched_body, dict)
    assert enriched_body["data"] == [
        {
            "id": "price_target",
            "product": "prod_target",
            "product_name": "Pro Monthly",
            "unit_amount": 7900,
            "currency": "usd",
        }
    ]


@pytest.mark.parametrize(
    ("query_price", "trusted_prices", "trusted_products", "error"),
    [
        (
            {"id": "price_query", "product": "prod_target"},
            {"price_trusted": {"id": "price_trusted", "product": "prod_target"}},
            {"prod_target": {"id": "prod_target", "name": "Pro Monthly"}},
            "different price IDs",
        ),
        (
            {"id": "price_target", "product": "prod_query"},
            {"price_target": {"id": "price_target", "product": "prod_trusted"}},
            {
                "prod_query": {"id": "prod_query", "name": "Pro Monthly"},
                "prod_trusted": {"id": "prod_trusted", "name": "Pro Monthly"},
            },
            "product relationship",
        ),
        (
            {"id": "price_target", "product": "prod_target"},
            {"price_target": {"id": "price_target", "product": "prod_target"}},
            {"prod_target": {"id": "prod_target", "name": ""}},
            "has no name",
        ),
    ],
)
def test_stripe_price_enrichment_rejects_untrusted_or_incomplete_joins(
    query_price: dict[str, Any],
    trusted_prices: dict[str, Any],
    trusted_products: dict[str, Any],
    error: str,
) -> None:
    capture = _query(
        "prices",
        provider="stripe",
        role="payments",
        canonicalizer="stripe_prices_stable",
        path="/v1/prices?limit=100",
        body={"object": "list", "data": [query_price], "has_more": False},
    )
    snapshot = _snapshot(
        provider="stripe",
        role="payments",
        state={
            "prices": trusted_prices,
            "products": trusted_products,
        },
        queries=[capture],
    )

    with pytest.raises(StateCaptureError, match=error):
        enrich_snapshot_from_trusted_state(snapshot)


def _github_snapshot(
    *,
    trusted_review_count: int = 1,
    include_comment_snapshot: bool = True,
    sibling_pull: int = 1,
) -> TrustedStateSnapshot:
    pulls = _query(
        "pulls",
        provider="github",
        role="code_host",
        canonicalizer="github_all_pull_review_artifacts",
        path="/repos/acme/web-parser/pulls",
        body=[{"id": 101, "number": 1, "title": "Parser fix"}],
    )
    reviews = _query(
        "reviews",
        provider="github",
        role="code_host",
        canonicalizer="github_reviews_stable",
        path=f"/repos/acme/web-parser/pulls/{sibling_pull}/reviews",
        body=[{"id": 201, "state": "CHANGES_REQUESTED"}],
    )
    queries = [pulls, reviews]
    if include_comment_snapshot:
        queries.append(
            _query(
                "comments",
                provider="github",
                role="code_host",
                canonicalizer="github_review_comments_stable",
                path=f"/repos/acme/web-parser/pulls/{sibling_pull}/comments",
                body=[{"id": 301, "body": "Replace eval."}],
            )
        )
    return _snapshot(
        provider="github",
        role="code_host",
        state={
            "summary": {
                "repos": [
                    {
                        "full_name": "acme/web-parser",
                        "reviews": trusted_review_count,
                    }
                ]
            }
        },
        queries=queries,
    )


def test_github_enrichment_joins_sibling_artifacts_and_proves_review_total() -> None:
    snapshot = _github_snapshot()

    enriched = enrich_snapshot_from_trusted_state(snapshot)

    body = enriched.queries["pulls"].body
    assert isinstance(body, list)
    assert body == [
        {
            "id": 101,
            "number": 1,
            "title": "Parser fix",
            "reviews": [{"id": 201, "state": "CHANGES_REQUESTED"}],
            "review_comments": [{"id": 301, "body": "Replace eval."}],
        }
    ]


@pytest.mark.parametrize(
    ("snapshot", "error"),
    [
        (
            _github_snapshot(include_comment_snapshot=False),
            "review and comment snapshot pull sets differ",
        ),
        (
            _github_snapshot(trusted_review_count=2),
            "trusted review total cannot prove",
        ),
        (
            _github_snapshot(sibling_pull=2),
            "review snapshot references a pull outside",
        ),
    ],
)
def test_github_enrichment_rejects_incomplete_or_out_of_scope_artifacts(
    snapshot: TrustedStateSnapshot,
    error: str,
) -> None:
    with pytest.raises(StateCaptureError, match=error):
        enrich_snapshot_from_trusted_state(snapshot)
