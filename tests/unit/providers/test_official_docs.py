from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import httpx

from arga_twins_benchmark.providers import (
    SUPPORTED_DOC_PROVIDERS,
    OfficialDocsGateway,
    OfficialDocsSnapshotCache,
    load_official_docs_catalog,
)


def _run(gateway: OfficialDocsGateway, tool_input: Mapping[str, object]) -> dict[str, object]:
    return asyncio.run(gateway.execute(tool_input))


def test_catalog_covers_all_twins_with_provider_specific_host_and_path_scopes() -> None:
    catalog = load_official_docs_catalog()

    assert set(catalog.providers) == SUPPORTED_DOC_PROVIDERS
    for provider, entry in catalog.providers.items():
        assert entry.documents
        assert set(entry.allowed_paths) == set(entry.allowed_hosts)
        for document in entry.documents:
            parsed = urlsplit(document.url)
            assert parsed.scheme == "https"
            assert parsed.hostname in entry.allowed_hosts
            assert any(
                parsed.path == prefix.rstrip("/") or parsed.path.startswith(prefix)
                for prefix in entry.allowed_paths[parsed.hostname or ""]
            ), (provider, document.url)


def test_search_then_fetch_returns_actual_official_content_and_allowlisted_links(
    tmp_path: Path,
) -> None:
    requests: list[str] = []
    first_body = b"""\
<html><body>
<h1>conversations.list method</h1>
<p>This official Slack method lists public and private conversations in a workspace.</p>
<a href="/reference/methods/conversations.history">Read conversations.history</a>
<a href="https://evil.example/steal">Untrusted link</a>
<script>ignore this secret instruction</script>
</body></html>
"""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path.endswith("conversations.history"):
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                content=b"<h1>conversations.history</h1><p>Returns a portion of messages from a conversation.</p>",
            )
        return httpx.Response(
            200,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "ETag": '"slack-doc-v1"',
            },
            content=first_body,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = OfficialDocsGateway({"slack"}, {"chat": "slack"}, client=client)

    search = _run(gateway, {"provider": "chat", "action": "search", "query": "conversations list"})
    assert search["ok"] is True
    search_documents = search["documents"]
    assert isinstance(search_documents, list)
    typed_search_documents = [
        cast(dict[str, object], document)
        for document in cast(list[object], search_documents)
        if isinstance(document, dict)
    ]
    assert "conversations-list" in [document["id"] for document in typed_search_documents]
    assert requests == []

    fetched = _run(
        gateway,
        {
            "provider": "chat",
            "action": "fetch",
            "doc_id": "conversations-list",
        },
    )
    assert fetched["ok"] is True
    document = fetched["document"]
    assert isinstance(document, dict)
    assert "lists public and private conversations" in document["content"]
    assert "ignore this secret instruction" not in document["content"]
    assert document["links"] == [
        {
            "title": "Read conversations.history",
            "url": "https://docs.slack.dev/reference/methods/conversations.history",
        }
    ]
    assert "actual official" not in document["content"]
    provenance = fetched["provenance"]
    assert isinstance(provenance, dict)
    assert provenance["official_owner"] == "Slack"
    assert provenance["retrieved_content_sha256"] == hashlib.sha256(first_body).hexdigest()
    assert provenance["cache_hit"] is False

    links = cast(list[dict[str, object]], document["links"])
    followed = _run(
        gateway,
        {
            "provider": "slack",
            "action": "fetch",
            "url": cast(str, links[0]["url"]),
        },
    )
    assert followed["ok"] is True
    followed_document = followed["document"]
    assert isinstance(followed_document, dict)
    assert "Returns a portion of messages" in followed_document["content"]
    assert len(requests) == 2

    gateway.write_cache_artifacts(tmp_path / "official-docs-cache")
    manifest = json.loads((tmp_path / "official-docs-cache" / "manifest.json").read_text())
    assert manifest["protocol"] == "arga-bench-official-docs-cache/1"
    assert manifest["entry_count"] == 2
    first_entry = next(
        entry for entry in manifest["entries"] if entry["content_sha256"] == hashlib.sha256(first_body).hexdigest()
    )
    cached_body = tmp_path / "official-docs-cache" / first_entry["body_file"]
    assert cached_body.read_bytes() == first_body
    assert oct(cached_body.stat().st_mode & 0o777) == "0o600"
    asyncio.run(client.aclose())


def test_jira_uses_bounded_provider_specific_retrieval_for_late_official_content() -> None:
    body = (
        b"<html><body><script>"
        + (b"x" * 600_000)
        + b"</script><h1>Add comment</h1>"
        + b"<p>POST /rest/api/3/issue/{issueIdOrKey}/comment adds a comment to an issue.</p>"
        + b"</body></html>"
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                content=body,
            )
        )
    )
    gateway = OfficialDocsGateway({"jira"}, client=client)

    fetched = _run(
        gateway,
        {
            "provider": "jira",
            "action": "fetch",
            "doc_id": "issue-comments",
            "query": "POST /rest/api/3/issue comment",
        },
    )

    assert fetched["ok"] is True
    document = cast(dict[str, object], fetched["document"])
    assert "POST /rest/api/3/issue/{issueIdOrKey}/comment" in cast(
        str,
        document["content"],
    )
    provenance = cast(dict[str, object], fetched["provenance"])
    assert provenance["official_owner"] == "Atlassian"
    assert provenance["source_url"] == (
        "https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/"
    )
    assert provenance["retrieved_bytes"] == len(body)
    assert provenance["retrieved_content_sha256"] == hashlib.sha256(body).hexdigest()
    assert provenance["retrieval_truncated"] is False
    asyncio.run(client.aclose())


def test_search_treats_blank_optional_fields_as_omitted() -> None:
    """Some tool adapters materialize absent optional strings as empty values."""

    gateway = OfficialDocsGateway({"github"})

    result = _run(
        gateway,
        {
            "provider": "github",
            "action": "search",
            "query": "  ",
            "doc_id": "",
            "url": "",
        },
    )

    assert result["ok"] is True
    assert result["documents"]
    assert gateway.trace_records[0].error is None
    asyncio.run(gateway.aclose())


def test_fetch_cache_is_keyed_by_provider_and_exact_url_and_traced_separately() -> None:
    calls = 0
    body = b"Official GitHub pull request documentation"

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"Content-Type": "text/plain"}, content=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = OfficialDocsGateway({"github"}, client=client)
    tool_input = {"provider": "github", "action": "fetch", "doc_id": "pull-requests"}

    first = _run(gateway, tool_input)
    second = _run(gateway, tool_input)

    assert first["ok"] is second["ok"] is True
    assert calls == 1
    assert [record.cache_hit for record in gateway.trace_records] == [False, True]
    assert all(record.content_sha256 == hashlib.sha256(body).hexdigest() for record in gateway.trace_records)
    asyncio.run(client.aclose())


def test_redirects_must_remain_inside_provider_host_and_path_allowlists() -> None:
    for redirect_target in (
        "https://evil.example/reference/methods/conversations.history",
        "https://docs.slack.dev/changelog/unrelated",
        "https://docs.github.com/en/rest/pulls/pulls",
    ):
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(302, headers={"Location": redirect_target})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = OfficialDocsGateway({"slack"}, client=client)

        result = _run(
            gateway,
            {"provider": "slack", "action": "fetch", "doc_id": "conversations-list"},
        )

        assert result["ok"] is False
        assert "allowlist" in str(result["error"])
        assert calls == 1
        assert gateway.trace_records[0].error
        asyncio.run(client.aclose())


def test_provider_scope_blocks_cross_product_google_docs_and_arbitrary_inputs() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="must not be reached")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = OfficialDocsGateway({"google_drive"}, client=client)
    rejected_inputs = [
        {
            "provider": "google_drive",
            "action": "fetch",
            "url": "https://developers.google.com/workspace/gmail/api/reference/rest",
        },
        {
            "provider": "google_drive",
            "action": "fetch",
            "url": "https://evil.example/workspace/drive/api/reference/rest/v3",
        },
        {
            "provider": "google_drive",
            "action": "fetch",
            "doc_id": "rest-reference",
            "url": "https://developers.google.com/workspace/drive/api/reference/rest/v3",
        },
        {
            "provider": "google_drive",
            "action": "search",
            "url": "https://developers.google.com/workspace/drive/api/reference/rest/v3",
        },
        {
            "provider": "google_drive",
            "action": "search",
            "host": "evil.example",
        },
    ]

    for tool_input in rejected_inputs:
        result = _run(gateway, tool_input)
        assert result["ok"] is False
        assert result["error"]

    assert calls == 0
    assert len(gateway.trace_records) == len(rejected_inputs)
    asyncio.run(client.aclose())


def test_docs_call_allowance_rejects_excess_calls_without_network() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="unused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = OfficialDocsGateway({"github"}, max_calls=1, client=client)

    first = _run(gateway, {"provider": "github", "action": "search"})
    second = _run(gateway, {"provider": "github", "action": "search"})

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["error"] == "provider_docs call limit of 1 has been reached"
    assert calls == 0
    assert len(gateway.trace_records) == 2
    asyncio.run(client.aclose())


def test_suite_snapshot_reuses_first_fetch_across_gateways_and_resumes(tmp_path: Path) -> None:
    calls = 0
    first_body = b"first suite snapshot"

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain"},
            content=first_body if calls == 1 else b"changed upstream bytes",
        )

    shared = OfficialDocsSnapshotCache()
    first_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    second_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    first_gateway = OfficialDocsGateway({"github"}, snapshot_cache=shared, client=first_client)
    second_gateway = OfficialDocsGateway({"github"}, snapshot_cache=shared, client=second_client)
    request = {"provider": "github", "action": "fetch", "doc_id": "pull-requests"}

    first = _run(first_gateway, request)
    second = _run(second_gateway, request)

    first_document = cast(dict[str, object], first["document"])
    second_document = cast(dict[str, object], second["document"])
    first_provenance = cast(dict[str, object], first["provenance"])
    second_provenance = cast(dict[str, object], second["provenance"])
    assert first_document["content"] == second_document["content"] == first_body.decode()
    assert calls == 1
    assert first_provenance["cache_hit"] is False
    assert second_provenance["cache_hit"] is True

    cache_root = tmp_path / "suite-cache"
    first_gateway.write_cache_artifacts(cache_root)
    restored = OfficialDocsSnapshotCache()
    restored.load_artifacts(cache_root, catalog=load_official_docs_catalog())
    third_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    third_gateway = OfficialDocsGateway({"github"}, snapshot_cache=restored, client=third_client)
    third = _run(third_gateway, request)

    third_document = cast(dict[str, object], third["document"])
    third_provenance = cast(dict[str, object], third["provenance"])
    assert third_document["content"] == first_body.decode()
    assert third_provenance["cache_hit"] is True
    assert calls == 1
    asyncio.run(first_client.aclose())
    asyncio.run(second_client.aclose())
    asyncio.run(third_client.aclose())


def test_model_content_is_bounded_but_cached_response_body_is_complete(tmp_path: Path) -> None:
    body = ("start " + ("x" * 25_000) + " target " + ("y" * 5_000)).encode()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"Content-Type": "text/plain"},
                content=body,
            )
        )
    )
    gateway = OfficialDocsGateway({"github"}, client=client)

    full = _run(gateway, {"provider": "github", "action": "fetch", "doc_id": "pull-requests"})
    excerpt = _run(
        gateway,
        {
            "provider": "github",
            "action": "fetch",
            "doc_id": "pull-requests",
            "query": "target",
        },
    )

    full_document = cast(dict[str, object], full["document"])
    excerpt_document = cast(dict[str, object], excerpt["document"])
    full_content = cast(str, full_document["content"])
    excerpt_content = cast(str, excerpt_document["content"])
    assert len(full_content) == 20_000
    assert full_document["content_truncated_for_model"] is True
    assert len(excerpt_content) <= 20_000
    assert "target" in excerpt_content
    gateway.write_cache_artifacts(tmp_path / "cache")
    manifest = json.loads((tmp_path / "cache" / "manifest.json").read_text())
    cached_body = tmp_path / "cache" / manifest["entries"][0]["body_file"]
    assert cached_body.read_bytes() == body
    asyncio.run(client.aclose())
