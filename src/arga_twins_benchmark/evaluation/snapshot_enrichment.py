from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, cast
from urllib.parse import urlsplit

from arga_twins_benchmark.evaluation.protocol import JsonValue
from arga_twins_benchmark.evaluation.state_capture import (
    CapturedProviderState,
    CapturedQueryState,
    StateCaptureError,
    TrustedStateSnapshot,
)

_SLACK_CANONICALIZERS = frozenset(
    {
        "slack_channels_messages_stable",
        "slack_channels_messages_v1",
    }
)
_GMAIL_CANONICALIZERS = frozenset(
    {
        "gmail_drafts_stable",
        "gmail_drafts_v1",
        "gmail_labels_stable",
        "gmail_labels_v1",
        "gmail_messages_labels_threads_v1",
        "gmail_messages_stable",
        "gmail_messages_threads_v1",
    }
)
_NOTION_CANONICALIZERS = frozenset(
    {
        "notion_pages_complete",
        "notion_pages_markdown_v1",
        "notion_spec_pages_stable",
    }
)


def enrich_snapshot_from_trusted_state(
    snapshot: TrustedStateSnapshot,
) -> TrustedStateSnapshot:
    """Complete known lossy data-plane reads from the paired trusted state.

    Some provider list endpoints intentionally return summaries even though the
    verifier canonicalizer covers nested resources. The trusted admin capture
    is allowed to complete those projections only through provider-specific,
    checked joins. Native response bodies remain unchanged in the artifact.
    """

    queries = dict(snapshot.queries)
    for query_id, capture in sorted(snapshot.queries.items()):
        provider = snapshot.providers[capture.provider_name]
        body = _enriched_body(
            capture=capture,
            provider=provider,
            snapshot=snapshot,
        )
        if body is not capture.body:
            queries[query_id] = replace(capture, body=body)
    return TrustedStateSnapshot(providers=dict(snapshot.providers), queries=queries)


def _enriched_body(
    *,
    capture: CapturedQueryState,
    provider: CapturedProviderState,
    snapshot: TrustedStateSnapshot,
) -> JsonValue:
    if capture.canonicalizer in _SLACK_CANONICALIZERS:
        if capture.provider_name != "slack":
            raise StateCaptureError("Slack canonicalizer is not paired with the Slack trusted state")
        return _enrich_slack(capture, provider.state)
    if capture.canonicalizer in _GMAIL_CANONICALIZERS:
        if capture.provider_name != "gmail":
            raise StateCaptureError("Gmail canonicalizer is not paired with the Gmail trusted state")
        return _enrich_gmail(capture, provider.state)
    if capture.canonicalizer in _NOTION_CANONICALIZERS:
        if capture.provider_name != "notion":
            raise StateCaptureError("Notion canonicalizer is not paired with the Notion trusted state")
        return _enrich_notion(capture, provider.state)
    if capture.canonicalizer == "github_all_pull_review_artifacts":
        if capture.provider_name != "github":
            raise StateCaptureError("GitHub canonicalizer is not paired with the GitHub trusted state")
        return _enrich_github_pull_artifacts(capture, provider.state, snapshot)
    return capture.body


def _object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StateCaptureError(f"{label} must be a JSON object")
    mapping = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise StateCaptureError(f"{label} must be a JSON object")
    return cast(dict[str, Any], mapping)


def _array(value: object, *, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise StateCaptureError(f"{label} must be a JSON array")
    return cast(list[Any], value)


def _stable_id(value: Mapping[str, Any], *, label: str) -> str:
    candidate = value.get("id")
    if not isinstance(candidate, str | int) or isinstance(candidate, bool) or not str(candidate):
        raise StateCaptureError(f"{label} has no stable id")
    return str(candidate)


def _index_by_id(values: Sequence[object], *, label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(values):
        item = _object(raw, label=f"{label}[{index}]")
        item_id = _stable_id(item, label=f"{label}[{index}]")
        if item_id in indexed:
            raise StateCaptureError(f"{label} contains duplicate id {item_id!r}")
        indexed[item_id] = item
    return indexed


def _enrich_slack(
    capture: CapturedQueryState,
    admin_state: Mapping[str, JsonValue],
) -> JsonValue:
    body = _object(capture.body, label="Slack conversations.list response")
    channels = _array(body.get("channels"), label="Slack conversations.list channels")
    query_channels = _index_by_id(channels, label="Slack conversations.list channels")
    admin_channels = _index_by_id(
        _array(admin_state.get("channels"), label="Slack trusted channels"),
        label="Slack trusted channels",
    )
    if set(query_channels) != set(admin_channels):
        raise StateCaptureError("Slack query and trusted state contain different channel IDs")

    messages_by_channel: dict[str, list[JsonValue]] = {channel_id: [] for channel_id in query_channels}
    seen_message_ids: set[tuple[str, str]] = set()
    events = _array(admin_state.get("events"), label="Slack trusted events")
    for index, raw_event in enumerate(events):
        event = _object(raw_event, label=f"Slack trusted event[{index}]")
        envelope = _object(event.get("envelope"), label=f"Slack trusted event[{index}].envelope")
        raw_message = envelope.get("event")
        if not isinstance(raw_message, dict):
            continue
        message = _object(
            cast(dict[object, object], raw_message),
            label=f"Slack trusted event[{index}].envelope.event",
        )
        if message.get("type") != "message":
            continue
        channel_id = message.get("channel")
        if not isinstance(channel_id, str) or channel_id not in messages_by_channel:
            raise StateCaptureError("Slack trusted message references a channel outside conversations.list")
        message_id = message.get("ts")
        text = message.get("text")
        user = message.get("user")
        if (
            not isinstance(message_id, str | int)
            or isinstance(message_id, bool)
            or not str(message_id)
            or not isinstance(text, str)
            or not text
            or not isinstance(user, str)
            or not user
        ):
            raise StateCaptureError("Slack trusted message lacks ts, channel, text, or user")
        identity = (channel_id, str(message_id))
        if identity in seen_message_ids:
            raise StateCaptureError(f"Slack trusted events contain duplicate message {identity!r}")
        seen_message_ids.add(identity)
        messages_by_channel[channel_id].append(cast(JsonValue, dict(message)))

    for channel_id, messages in messages_by_channel.items():
        trusted_count = admin_channels[channel_id].get("message_count")
        if isinstance(trusted_count, bool) or not isinstance(trusted_count, int):
            raise StateCaptureError(f"Slack trusted channel {channel_id!r} has no integer message_count")
        if trusted_count != len(messages):
            raise StateCaptureError(
                f"Slack trusted channel {channel_id!r} message_count differs from its message events"
            )
        messages.sort(
            key=lambda item: str(cast(dict[str, JsonValue], item).get("ts", "")) if isinstance(item, dict) else ""
        )

    return cast(
        JsonValue,
        {
            **body,
            "messages_by_channel": messages_by_channel,
        },
    )


def _enrich_gmail(
    capture: CapturedQueryState,
    admin_state: Mapping[str, JsonValue],
) -> JsonValue:
    body = _object(capture.body, label="Gmail list response")
    mailboxes = _object(admin_state.get("mailboxes"), label="Gmail trusted mailboxes")
    collection = _gmail_collection(capture.canonicalizer)
    query_items = _array(body.get(collection, []), label=f"Gmail query {collection}")
    query_ids = set(_index_by_id(query_items, label=f"Gmail query {collection}"))
    trusted_items: list[object] = []
    for mailbox_name, raw_mailbox in sorted(mailboxes.items()):
        mailbox = _object(raw_mailbox, label=f"Gmail trusted mailbox {mailbox_name!r}")
        trusted_items.extend(
            _array(
                mailbox.get(collection, []),
                label=f"Gmail trusted mailbox {mailbox_name!r}.{collection}",
            )
        )
    trusted_ids = set(_index_by_id(trusted_items, label=f"Gmail trusted {collection}"))
    if query_ids != trusted_ids:
        raise StateCaptureError(f"Gmail query and trusted state contain different {collection} IDs")
    return cast(JsonValue, {"mailboxes": mailboxes})


def _gmail_collection(canonicalizer: str) -> str:
    if "draft" in canonicalizer:
        return "drafts"
    if "label" in canonicalizer and not canonicalizer.startswith("gmail_messages"):
        return "labels"
    if "thread" in canonicalizer and canonicalizer.startswith("gmail_threads"):
        return "threads"
    return "messages"


def _enrich_notion(
    capture: CapturedQueryState,
    admin_state: Mapping[str, JsonValue],
) -> JsonValue:
    body = _object(capture.body, label="Notion search response")
    results = _array(body.get("results"), label="Notion search results")
    query_pages: list[object] = []
    for raw in results:
        if not isinstance(raw, dict):
            continue
        page = cast(dict[object, object], raw)
        if page.get("object") in (None, "page"):
            query_pages.append(page)
    trusted_pages = _array(admin_state.get("pages"), label="Notion trusted pages")
    query_index = _index_by_id(query_pages, label="Notion query pages")
    trusted_index = _index_by_id(trusted_pages, label="Notion trusted pages")
    query_ids = set(query_index)
    trusted_ids = set(trusted_index)
    if query_ids != trusted_ids:
        raise StateCaptureError("Notion query and trusted state contain different page IDs")
    enriched_pages: list[JsonValue] = []
    for page_id, trusted_page in sorted(trusted_index.items()):
        page = dict(trusted_page)
        supplemental_markdown = query_index[page_id].get("markdown")
        if supplemental_markdown is not None:
            if not isinstance(supplemental_markdown, str):
                raise StateCaptureError("Notion supplemental Markdown must be a string")
            page["markdown"] = supplemental_markdown
        enriched_pages.append(cast(JsonValue, page))
    return cast(JsonValue, {"pages": enriched_pages})


def _enrich_github_pull_artifacts(
    capture: CapturedQueryState,
    admin_state: Mapping[str, JsonValue],
    snapshot: TrustedStateSnapshot,
) -> JsonValue:
    pulls = _array(
        capture.body.get("pulls") if isinstance(capture.body, dict) else capture.body,
        label="GitHub pull artifact response",
    )
    pull_index = _index_by_id(pulls, label="GitHub pull artifacts")
    pulls_by_number: dict[int, dict[str, Any]] = {}
    for pull in pull_index.values():
        number = pull.get("number")
        if isinstance(number, bool) or not isinstance(number, int):
            raise StateCaptureError("GitHub pull artifact has no integer number")
        if number in pulls_by_number:
            raise StateCaptureError(f"GitHub pull artifacts contain duplicate number {number}")
        pulls_by_number[number] = pull

    reviews_by_pull: dict[int, list[Any]] = {}
    comments_by_pull: dict[int, list[Any]] = {}
    for sibling in snapshot.queries.values():
        if sibling.provider_name != capture.provider_name:
            continue
        pull_number = _github_pull_number(sibling.path)
        if pull_number is None:
            continue
        if sibling.canonicalizer == "github_reviews_stable":
            if pull_number in reviews_by_pull:
                raise StateCaptureError(f"GitHub has duplicate review snapshot for pull {pull_number}")
            reviews_by_pull[pull_number] = _array(
                sibling.body,
                label=f"GitHub pull {pull_number} reviews",
            )
        elif sibling.canonicalizer == "github_review_comments_stable":
            if pull_number in comments_by_pull:
                raise StateCaptureError(f"GitHub has duplicate comment snapshot for pull {pull_number}")
            comments_by_pull[pull_number] = _array(
                sibling.body,
                label=f"GitHub pull {pull_number} review comments",
            )
    if set(reviews_by_pull) != set(comments_by_pull):
        raise StateCaptureError("GitHub review and comment snapshot pull sets differ")
    if not set(reviews_by_pull) <= set(pulls_by_number):
        raise StateCaptureError("GitHub review snapshot references a pull outside the pull list")

    repository = _github_repository(capture.path)
    summary = _github_repository_summary(admin_state, repository=repository)
    total_reviews = summary.get("reviews")
    sibling_review_count = sum(len(values) for values in reviews_by_pull.values())
    if isinstance(total_reviews, bool) or not isinstance(total_reviews, int) or total_reviews != sibling_review_count:
        raise StateCaptureError("GitHub trusted review total cannot prove that non-target pulls have no reviews")

    enriched: list[JsonValue] = []
    for number, raw_pull in sorted(pulls_by_number.items()):
        pull = dict(raw_pull)
        pull["reviews"] = reviews_by_pull.get(number, [])
        pull["review_comments"] = comments_by_pull.get(number, [])
        enriched.append(cast(JsonValue, pull))
    return enriched


def _github_pull_number(path: str) -> int | None:
    parts = [part for part in urlsplit(path).path.split("/") if part]
    try:
        pull_index = parts.index("pulls")
    except ValueError:
        return None
    if pull_index + 1 >= len(parts):
        return None
    try:
        return int(parts[pull_index + 1])
    except ValueError:
        return None


def _github_repository(path: str) -> str:
    parts = [part for part in urlsplit(path).path.split("/") if part]
    if len(parts) < 3 or parts[0] != "repos":
        raise StateCaptureError("GitHub pull artifact path does not identify a repository")
    return f"{parts[1]}/{parts[2]}"


def _github_repository_summary(
    admin_state: Mapping[str, JsonValue],
    *,
    repository: str,
) -> dict[str, Any]:
    summary = _object(admin_state.get("summary"), label="GitHub trusted summary")
    repos = _array(summary.get("repos"), label="GitHub trusted summary.repos")
    matches: list[dict[str, Any]] = []
    for raw in repos:
        if not isinstance(raw, dict):
            continue
        raw_mapping = cast(dict[object, object], raw)
        if raw_mapping.get("full_name") == repository:
            matches.append(_object(raw_mapping, label="GitHub trusted repository summary"))
    if len(matches) != 1:
        raise StateCaptureError(f"GitHub trusted summary does not contain exactly one {repository!r}")
    return matches[0]
