from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlsplit

from arga_twins_benchmark.evaluation.protocol import JsonValue
from arga_twins_benchmark.evaluation.state_capture import (
    CapturedQueryState,
    StateCaptureError,
    TrustedStateSnapshot,
)

_DISCORD_CHANNELS_PATH = re.compile(r"^/api/v10/guilds/([^/]+)/channels$")
_DISCORD_MESSAGES_PATH = re.compile(r"^/api/v10/channels/([^/]+)/messages$")
_NOTION_BLOCKS_PATH = re.compile(r"^/v1/blocks/([^/]+)/children$")
_CONTENT_MARKER_RE = re.compile(
    r"(?:content[\s_-]*marker\s*[:=]|marker\s*[:=])\s*([A-Za-z0-9._:-]+)",
    re.IGNORECASE,
)
_MARKER_TRAILING_PUNCTUATION = ".,;:!?)]}"
_MUTATING_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


@dataclass(frozen=True)
class RecordedProviderCall:
    call_index: int
    provider: str
    requested_provider: str
    method: str
    path: str
    status_code: int
    arguments: Mapping[str, Any]
    response_body: object
    truncated: bool


def recover_preserved_trial_snapshots(
    *,
    baseline: TrustedStateSnapshot,
    final: TrustedStateSnapshot,
    invocation: Mapping[str, Any],
    trace_payload: Mapping[str, Any],
    control_payload: Mapping[str, Any],
    instance_id: str,
    instance_path: Path,
    seed_files: Mapping[str, str],
    expected_episode_hash: str,
) -> tuple[TrustedStateSnapshot, TrustedStateSnapshot]:
    """Recover legacy suite projections from hash-bound catalog and call evidence.

    The July 2026 suite preserved complete candidate tool responses but a few
    verifier list queries were too shallow. This adapter is intentionally
    limited to those artifacts. It cross-checks the exact Scenario hash,
    invocation responses, provider trace, trusted admin counters, and catalog
    seed before completing the snapshots.
    """

    if control_payload.get("instance_id") != instance_id:
        raise StateCaptureError("preserved control artifact has a different instance_id")
    if control_payload.get("scenario_content_sha256") != expected_episode_hash:
        raise StateCaptureError("preserved Scenario hash differs from the catalog episode hash")
    seed_root = instance_path.parent
    seed_payloads = {
        provider: _read_seed(
            _resolve_seed_path(seed_root, relative_path, provider=provider),
            provider=provider,
        )
        for provider, relative_path in seed_files.items()
    }
    calls = _recorded_provider_calls(invocation, trace_payload)

    recovered_baseline = _recover_drive_markers(
        baseline,
        seed_payloads.get("google_drive"),
    )
    recovered_final = _recover_drive_markers(
        final,
        seed_payloads.get("google_drive"),
    )
    recovered_baseline, recovered_final = _recover_notion_markdown(
        baseline=recovered_baseline,
        final=recovered_final,
        seed=seed_payloads.get("notion"),
        calls=calls,
    )
    recovered_baseline, recovered_final = _recover_discord(
        baseline=recovered_baseline,
        final=recovered_final,
        seed=seed_payloads.get("discord"),
        calls=calls,
    )
    return recovered_baseline, recovered_final


def _read_seed(path: Path, *, provider: str) -> JsonValue:
    if path.is_symlink():
        raise StateCaptureError(f"{provider} seed may not be a symlink")
    try:
        payload: object = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StateCaptureError(f"cannot read hash-bound {provider} seed") from error
    return cast(JsonValue, payload)


def _resolve_seed_path(seed_root: Path, relative_path: str, *, provider: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise StateCaptureError(f"{provider} seed path escapes its catalog instance")
    try:
        resolved_root = seed_root.resolve(strict=True)
        candidate = seed_root / relative
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise StateCaptureError(f"cannot resolve hash-bound {provider} seed") from error
    if not resolved.is_relative_to(resolved_root):
        raise StateCaptureError(f"{provider} seed path escapes its catalog instance")
    current = candidate
    while current != seed_root:
        if current.is_symlink():
            raise StateCaptureError(f"{provider} seed may not traverse a symlink")
        current = current.parent
    return resolved


def _recorded_provider_calls(
    invocation: Mapping[str, Any],
    trace_payload: Mapping[str, Any],
) -> list[RecordedProviderCall]:
    raw_trace = trace_payload.get("events")
    raw_invocation = invocation.get("events")
    if not isinstance(raw_trace, list) or not isinstance(raw_invocation, list):
        raise StateCaptureError("preserved invocation or provider trace has no event list")
    trace_by_sequence: dict[int, dict[str, Any]] = {}
    for raw in cast(list[object], raw_trace):
        event = _string_object(raw, label="provider trace event")
        sequence = event.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence in trace_by_sequence:
            raise StateCaptureError("provider trace sequences are invalid or duplicated")
        trace_by_sequence[sequence] = event
    if sorted(trace_by_sequence) != list(range(1, len(trace_by_sequence) + 1)):
        raise StateCaptureError("provider trace sequences must be contiguous and one-based")

    calls: list[RecordedProviderCall] = []
    seen_sequences: set[int] = set()
    seen_call_indices: set[int] = set()
    previous_call_index = 0
    for event_offset, raw in enumerate(cast(list[object], raw_invocation), start=1):
        event = _string_object(raw, label=f"invocation event {event_offset}")
        if event.get("type") != "tool_call":
            continue
        output = _string_object(event.get("output"), label="invocation tool output")
        raw_embedded_trace = output.get("trace")
        if raw_embedded_trace is None:
            continue
        embedded = _string_object(raw_embedded_trace, label="invocation embedded trace")
        sequence = embedded.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise StateCaptureError("invocation embedded trace has an invalid sequence")
        authoritative = trace_by_sequence.get(sequence)
        if authoritative is None or sequence in seen_sequences:
            raise StateCaptureError("invocation trace sequence is absent or duplicated")
        seen_sequences.add(sequence)
        for field_name in (
            "requested_provider",
            "provider",
            "method",
            "path",
            "status_code",
            "operation",
            "operation_type",
        ):
            if embedded.get(field_name) != authoritative.get(field_name):
                raise StateCaptureError(f"invocation and provider trace differ on event {sequence} field {field_name}")
        for field_name in (
            "requested_provider",
            "provider",
            "method",
            "path",
            "status_code",
        ):
            if output.get(field_name) != authoritative.get(field_name):
                raise StateCaptureError(
                    f"invocation output and provider trace differ on event {sequence} field {field_name}"
                )
        output_truncated = output.get("truncated", False)
        trace_truncated = authoritative.get("truncated", False)
        if not isinstance(output_truncated, bool) or not isinstance(trace_truncated, bool):
            raise StateCaptureError("provider truncation flags must be Boolean")
        if output_truncated != trace_truncated:
            raise StateCaptureError("invocation output and provider trace differ on truncation")

        provider = authoritative.get("provider")
        requested = authoritative.get("requested_provider")
        method = authoritative.get("method")
        path = authoritative.get("path")
        status_code = authoritative.get("status_code")
        call_index = event.get("provider_call_index")
        if (
            isinstance(call_index, bool)
            or not isinstance(call_index, int)
            or call_index <= previous_call_index
            or call_index in seen_call_indices
        ):
            raise StateCaptureError("invocation provider_call_index values must be positive, unique, and increasing")
        seen_call_indices.add(call_index)
        previous_call_index = call_index

        # Some preserved transcripts contain failed tool attempts rejected before
        # an HTTP request was assembled. They remain part of the authoritative
        # trace, but a null method/path/status cannot support state recovery.
        if status_code is None:
            if not (
                isinstance(provider, str)
                and isinstance(requested, str)
                and (method is None or path is None)
                and isinstance(authoritative.get("error"), str)
                and authoritative.get("error")
            ):
                raise StateCaptureError("provider trace has a partially formed call")
            continue
        if (
            not isinstance(provider, str)
            or not isinstance(requested, str)
            or not isinstance(method, str)
            or not isinstance(path, str)
            or isinstance(status_code, bool)
            or not isinstance(status_code, int)
        ):
            raise StateCaptureError("provider trace event cannot be used as recovery evidence")

        arguments = _string_object(event.get("arguments"), label="invocation tool arguments")
        argument_provider = arguments.get("provider")
        argument_method = arguments.get("method")
        argument_path = arguments.get("path")
        if (
            argument_provider != requested
            or not isinstance(argument_method, str)
            or argument_method.upper() != method.upper()
            or not isinstance(argument_path, str)
            or urlsplit(argument_path).path != urlsplit(path).path
        ):
            raise StateCaptureError("invocation tool arguments differ from the provider trace")
        calls.append(
            RecordedProviderCall(
                call_index=call_index,
                provider=provider,
                requested_provider=requested,
                method=method.upper(),
                path=path,
                status_code=status_code,
                arguments=arguments,
                response_body=output.get("body"),
                truncated=trace_truncated,
            )
        )
    if seen_sequences != set(trace_by_sequence):
        raise StateCaptureError("provider trace contains events absent from the invocation transcript")
    return sorted(calls, key=lambda call: call.call_index)


def _recover_drive_markers(
    snapshot: TrustedStateSnapshot,
    seed: JsonValue | None,
) -> TrustedStateSnapshot:
    captures = [
        capture
        for capture in snapshot.queries.values()
        if capture.provider_name == "google_drive"
        and capture.canonicalizer in {"drive_files_content_hash_v1", "google_drive_state_stable"}
    ]
    if not captures or seed is None:
        return snapshot
    seed_files = _seed_drive_files(seed)
    if not seed_files:
        raise StateCaptureError("Google Drive seed contains no recoverable files")
    by_digest: dict[str, tuple[str, str | None]] = {}
    for name, content in seed_files:
        digest = hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()
        value = (name, _content_marker(content))
        if digest in by_digest and by_digest[digest] != value:
            raise StateCaptureError("Drive seed has an ambiguous content digest")
        by_digest[digest] = value

    queries = dict(snapshot.queries)
    for capture in captures:
        if not isinstance(capture.body, dict):
            raise StateCaptureError("Drive snapshot body must be an object")
        body = cast(dict[str, Any], capture.body)
        raw_files = body.get("files")
        if not isinstance(raw_files, list):
            raise StateCaptureError("Drive snapshot body has no files")
        enriched_files: list[JsonValue] = []
        for raw in cast(list[object], raw_files):
            file = _string_object(raw, label="Drive snapshot file")
            digest = file.get("md5Checksum")
            enriched = dict(file)
            normalized_digest = digest.lower() if isinstance(digest, str) else None
            existing = enriched.get("content_marker")
            if normalized_digest is not None and normalized_digest in by_digest:
                seed_name, marker = by_digest[normalized_digest]
                if enriched.get("name") != seed_name:
                    raise StateCaptureError("Drive seed digest resolves to a different file name")
                if existing is not None and existing != marker:
                    raise StateCaptureError("Drive snapshot content marker contradicts the hash-bound seed")
                if marker is not None:
                    enriched["content_marker"] = marker
            elif existing is not None:
                raise StateCaptureError("Drive snapshot content marker is not bound to the catalog seed")
            enriched_files.append(cast(JsonValue, enriched))
        queries[capture.query_id] = replace(
            capture,
            body=cast(JsonValue, {**body, "files": enriched_files}),
        )
    return TrustedStateSnapshot(providers=dict(snapshot.providers), queries=queries)


def _seed_drive_files(seed: JsonValue) -> list[tuple[str, str]]:
    root = _string_object(seed, label="Google Drive seed")
    result: list[tuple[str, str]] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            item = cast(dict[object, object], value)
            name = item.get("name")
            content = item.get("content")
            if isinstance(name, str) and isinstance(content, str):
                result.append((name, content))
            for child in item.values():
                visit(child)
        elif isinstance(value, list):
            for child in cast(list[object], value):
                visit(child)

    visit(root)
    return result


def _content_marker(content: str) -> str | None:
    marker_match = _CONTENT_MARKER_RE.search(content)
    if marker_match is None:
        return None
    marker = marker_match.group(1).rstrip(_MARKER_TRAILING_PUNCTUATION)
    if not marker:
        raise StateCaptureError("Drive seed contains an empty content marker")
    return marker


def _recover_notion_markdown(
    *,
    baseline: TrustedStateSnapshot,
    final: TrustedStateSnapshot,
    seed: JsonValue | None,
    calls: Sequence[RecordedProviderCall],
) -> tuple[TrustedStateSnapshot, TrustedStateSnapshot]:
    canonicalizer = "notion_pages_markdown_v1"
    baseline_captures = [capture for capture in baseline.queries.values() if capture.canonicalizer == canonicalizer]
    final_captures = [capture for capture in final.queries.values() if capture.canonicalizer == canonicalizer]
    if not baseline_captures and not final_captures:
        return baseline, final
    if seed is None or len(baseline_captures) != 1 or len(final_captures) != 1:
        raise StateCaptureError("Notion Markdown recovery requires one query and one seed")
    seed_pages = _seed_notion_pages(seed)
    baseline_capture = baseline_captures[0]
    final_capture = final_captures[0]
    baseline_pages = _notion_query_pages(baseline_capture)
    final_pages = _notion_query_pages(final_capture)
    if set(baseline_pages) != set(final_pages):
        raise StateCaptureError("Notion baseline and final page IDs differ")
    title_by_id = {page_id: _notion_page_title(page) for page_id, page in baseline_pages.items()}
    if len(set(title_by_id.values())) != len(title_by_id):
        raise StateCaptureError("Notion snapshot page titles are not unique")
    if set(title_by_id.values()) != set(seed_pages):
        raise StateCaptureError("Notion query titles differ from the hash-bound seed")

    first_blocks: dict[str, tuple[int, str]] = {}
    last_blocks: dict[str, tuple[int, str]] = {}
    modifying_calls: dict[str, list[int]] = {}
    for call in calls:
        if call.provider != "notion" or not 200 <= call.status_code <= 399:
            continue
        match = _NOTION_BLOCKS_PATH.fullmatch(urlsplit(call.path).path)
        if call.method == "GET" and match is not None and 200 <= call.status_code <= 299:
            if call.truncated:
                raise StateCaptureError("Notion block read is truncated")
            page_id = unquote(match.group(1))
            markdown = _notion_blocks_markdown(call.response_body)
            first_blocks.setdefault(page_id, (call.call_index, markdown))
            last_blocks[page_id] = (call.call_index, markdown)
        elif call.method in _MUTATING_METHODS:
            if _notion_read_only_post(call):
                continue
            if call.truncated:
                raise StateCaptureError("Notion mutation response is truncated")
            page_path_id = _notion_page_mutation_id(call)
            if page_path_id is not None:
                if page_path_id not in baseline_pages:
                    raise StateCaptureError("Notion mutation references a page outside the snapshot")
                continue
            page_id = _notion_affected_page(call)
            if page_id is None:
                raise StateCaptureError("Notion mutation cannot be bound to a snapshot page")
            if page_id not in baseline_pages:
                raise StateCaptureError("Notion mutation references a page outside the snapshot")
            modifying_calls.setdefault(page_id, []).append(call.call_index)

    baseline_markdown = {page_id: seed_pages[title] for page_id, title in title_by_id.items()}
    final_markdown = dict(baseline_markdown)
    for page_id, (first_index, first_markdown) in first_blocks.items():
        if page_id not in baseline_markdown:
            raise StateCaptureError("Notion block read references a page outside the snapshot")
        writes = modifying_calls.get(page_id, [])
        if writes and first_index > min(writes):
            raise StateCaptureError("Notion has no pre-mutation block read")
        if first_markdown != baseline_markdown[page_id]:
            raise StateCaptureError("Notion pre-mutation blocks differ from the hash-bound seed")
    for page_id, writes in modifying_calls.items():
        final_read = last_blocks.get(page_id)
        if final_read is None or final_read[0] <= max(writes):
            raise StateCaptureError("Notion mutation has no post-mutation block confirmation")
        final_markdown[page_id] = final_read[1]

    return (
        _inject_notion_markdown(baseline, baseline_capture.query_id, baseline_markdown),
        _inject_notion_markdown(final, final_capture.query_id, final_markdown),
    )


def _seed_notion_pages(seed: JsonValue) -> dict[str, str]:
    root = _string_object(seed, label="Notion seed")
    raw_pages = root.get("pages")
    if not isinstance(raw_pages, list):
        raise StateCaptureError("Notion seed has no pages")
    result: dict[str, str] = {}
    for raw in cast(list[object], raw_pages):
        page = _string_object(raw, label="Notion seed page")
        title = page.get("title")
        content = page.get("content")
        if not isinstance(title, str) or not isinstance(content, str) or title in result:
            raise StateCaptureError("Notion seed page has invalid or duplicate title/content")
        result[title] = content
    return result


def _notion_query_pages(capture: CapturedQueryState) -> dict[str, dict[str, Any]]:
    body = _string_object(capture.body, label="Notion snapshot query")
    raw_pages = body.get("results")
    if not isinstance(raw_pages, list):
        raise StateCaptureError("Notion snapshot query has no results")
    result: dict[str, dict[str, Any]] = {}
    for raw in cast(list[object], raw_pages):
        page = _string_object(raw, label="Notion snapshot page")
        page_id = page.get("id")
        if not isinstance(page_id, str) or page_id in result:
            raise StateCaptureError("Notion snapshot page has invalid or duplicate ID")
        result[page_id] = page
    return result


def _notion_page_title(page: Mapping[str, Any]) -> str:
    properties = page.get("properties")
    if not isinstance(properties, dict):
        raise StateCaptureError("Notion snapshot page has no properties")
    for raw_property in cast(dict[object, object], properties).values():
        if not isinstance(raw_property, dict):
            continue
        prop = cast(dict[str, Any], raw_property)
        rich_text = prop.get("title")
        if isinstance(rich_text, list):
            title = _notion_rich_text(cast(list[object], rich_text))
            if title:
                return title
    raise StateCaptureError("Notion snapshot page has no title")


def _inject_notion_markdown(
    snapshot: TrustedStateSnapshot,
    query_id: str,
    markdown_by_id: Mapping[str, str],
) -> TrustedStateSnapshot:
    capture = snapshot.queries[query_id]
    body = _string_object(capture.body, label="Notion snapshot query")
    raw_results = body.get("results")
    if not isinstance(raw_results, list):
        raise StateCaptureError("Notion snapshot query has no results")
    results: list[JsonValue] = []
    seen: set[str] = set()
    for raw in cast(list[object], raw_results):
        page = _string_object(raw, label="Notion snapshot page")
        page_id = page.get("id")
        if not isinstance(page_id, str) or page_id not in markdown_by_id:
            raise StateCaptureError("Notion Markdown recovery cannot resolve a page")
        results.append(cast(JsonValue, {**page, "markdown": markdown_by_id[page_id]}))
        seen.add(page_id)
    if seen != set(markdown_by_id):
        raise StateCaptureError("Notion Markdown recovery did not cover every page")
    queries = dict(snapshot.queries)
    queries[query_id] = replace(
        capture,
        body=cast(JsonValue, {**body, "results": results}),
    )
    return TrustedStateSnapshot(providers=dict(snapshot.providers), queries=queries)


def _notion_blocks_markdown(body: object) -> str:
    payload = _string_object(body, label="Notion block-list response")
    if payload.get("has_more") is not False or payload.get("next_cursor") is not None:
        raise StateCaptureError("Notion block-list response is incomplete")
    raw_blocks = payload.get("results")
    if not isinstance(raw_blocks, list):
        raise StateCaptureError("Notion block-list response has no results")
    rendered: list[tuple[str, str]] = []
    seen_block_ids: set[str] = set()
    for raw in cast(list[object], raw_blocks):
        block = _string_object(raw, label="Notion block")
        block_id = block.get("id")
        if not isinstance(block_id, str) or block_id in seen_block_ids:
            raise StateCaptureError("Notion block has an invalid or duplicate ID")
        seen_block_ids.add(block_id)
        if block.get("in_trash") is True or block.get("is_archived") is True:
            continue
        block_type = block.get("type")
        if not isinstance(block_type, str):
            raise StateCaptureError("Notion block has no type")
        content = block.get(block_type)
        content_mapping = _string_object(content, label=f"Notion {block_type} block")
        text = _notion_rich_text(content_mapping.get("rich_text"))
        if block_type.startswith("heading_"):
            level = int(block_type.removeprefix("heading_"))
            rendered.append(("paragraph", f"{'#' * level} {text}"))
        elif block_type == "paragraph":
            rendered.append(("paragraph", text))
        elif block_type == "numbered_list_item":
            rendered.append(("numbered", f"1. {text}"))
        elif block_type == "bulleted_list_item":
            rendered.append(("bulleted", f"- {text}"))
        else:
            raise StateCaptureError(f"unsupported Notion block type in preserved evidence: {block_type}")
    groups: list[str] = []
    active_kind: str | None = None
    active_lines: list[str] = []
    for kind, line in rendered:
        if active_lines and (kind == "paragraph" or kind != active_kind):
            groups.append("\n".join(active_lines))
            active_lines = []
        active_kind = kind
        active_lines.append(line)
    if active_lines:
        groups.append("\n".join(active_lines))
    return "\n\n".join(groups) + ("\n" if groups else "")


def _notion_rich_text(value: object) -> str:
    if not isinstance(value, list):
        raise StateCaptureError("Notion rich text must be an array")
    parts: list[str] = []
    for raw in cast(list[object], value):
        item = _string_object(raw, label="Notion rich-text item")
        plain = item.get("plain_text")
        if isinstance(plain, str):
            parts.append(plain)
            continue
        text = item.get("text")
        if isinstance(text, dict):
            text_mapping = cast(dict[object, object], text)
            content = text_mapping.get("content")
            if isinstance(content, str):
                parts.append(content)
                continue
        raise StateCaptureError("Notion rich-text item has no text content")
    return "".join(parts)


def _notion_read_only_post(call: RecordedProviderCall) -> bool:
    if call.method != "POST":
        return False
    parts = [part for part in urlsplit(call.path).path.split("/") if part]
    return parts == ["v1", "search"] or (
        len(parts) == 4 and parts[0] == "v1" and parts[1] in {"data_sources", "databases"} and parts[3] == "query"
    )


def _notion_page_mutation_id(call: RecordedProviderCall) -> str | None:
    parts = [part for part in urlsplit(call.path).path.split("/") if part]
    if len(parts) == 3 and parts[:2] == ["v1", "pages"]:
        return unquote(parts[2])
    return None


def _notion_affected_page(call: RecordedProviderCall) -> str | None:
    parsed = [part for part in urlsplit(call.path).path.split("/") if part]
    if len(parsed) < 3 or parsed[0] != "v1" or parsed[1] != "blocks":
        return None
    block_or_page_id = unquote(parsed[2])
    if len(parsed) >= 4 and parsed[3] == "children":
        return block_or_page_id
    response = call.response_body
    if isinstance(response, dict):
        response_mapping = cast(dict[object, object], response)
        parent = response_mapping.get("parent")
        if isinstance(parent, dict):
            parent_mapping = cast(dict[object, object], parent)
            page_id = parent_mapping.get("page_id")
            if isinstance(page_id, str):
                return page_id
    return None


def _recover_discord(
    *,
    baseline: TrustedStateSnapshot,
    final: TrustedStateSnapshot,
    seed: JsonValue | None,
    calls: Sequence[RecordedProviderCall],
) -> tuple[TrustedStateSnapshot, TrustedStateSnapshot]:
    canonicalizers = {
        "discord_guild_channels_messages_stable",
        "discord_guild_channels_messages_v1",
    }
    baseline_captures = [capture for capture in baseline.queries.values() if capture.canonicalizer in canonicalizers]
    final_captures = [capture for capture in final.queries.values() if capture.canonicalizer in canonicalizers]
    if not baseline_captures and not final_captures:
        return baseline, final
    if seed is None or len(baseline_captures) != 1 or len(final_captures) != 1:
        raise StateCaptureError("Discord recovery requires one snapshot query and one seed")
    baseline_capture = baseline_captures[0]
    final_capture = final_captures[0]
    seed_guilds = _seed_discord(seed)
    query_guilds = _discord_query_guilds(baseline_capture)
    if set(query_guilds) != set(seed_guilds):
        raise StateCaptureError("Discord query guild names differ from the hash-bound seed")
    final_query_guilds = _discord_query_guilds(final_capture)
    if query_guilds != final_query_guilds:
        raise StateCaptureError("Discord baseline and final guild lists differ")
    guild_name_by_id: dict[str, str] = {}
    expected_channels: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for guild_name, query_guild in query_guilds.items():
        guild_id = query_guild.get("id")
        if not isinstance(guild_id, str) or guild_id in guild_name_by_id:
            raise StateCaptureError("Discord query has invalid or duplicate guild IDs")
        guild_name_by_id[guild_id] = guild_name
        seed_channels = cast(dict[str, list[dict[str, Any]]], seed_guilds[guild_name]["channels"])
        for channel_name, seed_messages in seed_channels.items():
            expected_channels[(guild_id, channel_name)] = seed_messages

    successful = [call for call in calls if call.provider == "discord" and 200 <= call.status_code <= 399]
    channel_objects: dict[str, dict[str, Any]] = {}
    html_reads: list[tuple[int, str, str, list[dict[str, Any]]]] = []
    for call in successful:
        path = urlsplit(call.path).path
        channel_match = _DISCORD_CHANNELS_PATH.fullmatch(path)
        if call.method == "GET" and channel_match is not None and 200 <= call.status_code <= 299:
            if call.truncated:
                raise StateCaptureError("Discord channel read is truncated")
            request_guild_id = unquote(channel_match.group(1))
            if request_guild_id not in guild_name_by_id:
                raise StateCaptureError("Discord channel read references an unknown guild")
            raw_channels = _object_array(call.response_body, label="Discord channel response")
            for channel in raw_channels:
                channel_id = channel.get("id")
                response_guild_id = channel.get("guild_id")
                if not isinstance(channel_id, str) or response_guild_id not in (None, request_guild_id):
                    raise StateCaptureError("Discord channel response has an invalid identity")
                normalized = {**channel, "guild_id": request_guild_id}
                prior = channel_objects.setdefault(channel_id, normalized)
                if prior.get("name") != normalized.get("name") or prior.get("guild_id") != normalized.get("guild_id"):
                    raise StateCaptureError("Discord channel identity changed during the trial")
        elif (
            call.method == "GET"
            and path == "/"
            and 200 <= call.status_code <= 299
            and isinstance(call.response_body, str)
        ):
            if call.truncated:
                raise StateCaptureError("Discord HTML read is truncated")
            parsed = _discord_html_messages(call.response_body)
            if parsed is not None:
                html_reads.append((call.call_index, *parsed))

    if not channel_objects and html_reads:
        if len(expected_channels) != 1:
            raise StateCaptureError("Discord HTML recovery is ambiguous across multiple channels")
        _, channel_id, channel_name, _ = html_reads[0]
        guild_id, expected_name = next(iter(expected_channels))
        if channel_name != expected_name:
            raise StateCaptureError("Discord HTML channel differs from the hash-bound seed")
        channel_objects[channel_id] = {
            "id": channel_id,
            "name": channel_name,
            "guild_id": guild_id,
        }
    channels_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for channel in channel_objects.values():
        channel_name = channel.get("name")
        guild_id = channel.get("guild_id")
        if not isinstance(channel_name, str) or not isinstance(guild_id, str):
            raise StateCaptureError("Discord recovered channel has an invalid identity")
        key = (guild_id, channel_name)
        if key in channels_by_key:
            raise StateCaptureError("Discord channel reads contain duplicate names within a guild")
        channels_by_key[key] = channel
    if set(channels_by_key) != set(expected_channels):
        raise StateCaptureError("Discord channel reads differ from the hash-bound seed")

    native_reads: dict[str, list[tuple[int, list[dict[str, Any]]]]] = {}
    message_writes: dict[str, list[RecordedProviderCall]] = {}
    for call in successful:
        path = urlsplit(call.path).path
        match = _DISCORD_MESSAGES_PATH.fullmatch(path)
        if match is None:
            if call.method == "POST" and path == "/ui/messages":
                if call.truncated:
                    raise StateCaptureError("Discord message mutation response is truncated")
                body = _string_object(
                    call.arguments.get("body"),
                    label="Discord UI message arguments",
                )
                channel_id = body.get("channel_id")
                if not isinstance(channel_id, str):
                    raise StateCaptureError("Discord UI message has no channel ID")
                _discord_write_content(call)
                message_writes.setdefault(channel_id, []).append(call)
            elif call.method in _MUTATING_METHODS:
                raise StateCaptureError("unsupported successful Discord mutation in recovery evidence")
            continue
        channel_id = unquote(match.group(1))
        if call.method == "GET" and 200 <= call.status_code <= 299:
            if call.truncated:
                raise StateCaptureError("Discord message read is truncated")
            native_reads.setdefault(channel_id, []).append(
                (call.call_index, _object_array(call.response_body, label="Discord message response"))
            )
        elif call.method == "POST" and 200 <= call.status_code <= 299:
            if call.truncated:
                raise StateCaptureError("Discord message mutation response is truncated")
            _discord_write_content(call)
            message_writes.setdefault(channel_id, []).append(call)
        elif call.method in _MUTATING_METHODS:
            raise StateCaptureError("unsupported successful Discord mutation in recovery evidence")

    baseline_messages: dict[str, list[dict[str, Any]]] = {}
    final_messages: dict[str, list[dict[str, Any]]] = {}
    known_channel_ids = {
        cast(str, channel["id"]) for channel in channels_by_key.values() if isinstance(channel.get("id"), str)
    }
    if set(native_reads) - known_channel_ids or set(message_writes) - known_channel_ids:
        raise StateCaptureError("Discord message evidence references an unknown channel")
    for (guild_id, channel_name), channel in channels_by_key.items():
        channel_id = cast(str, channel["id"])
        seed_messages = expected_channels[(guild_id, channel_name)]
        writes = sorted(message_writes.get(channel_id, []), key=lambda call: call.call_index)
        matching_html = [
            (index, messages)
            for index, html_channel_id, html_name, messages in html_reads
            if html_channel_id == channel_id and html_name == channel_name
        ]
        reads = sorted([*native_reads.get(channel_id, []), *matching_html])
        first_write = writes[0].call_index if writes else None
        baseline_candidates = [
            (index, messages) for index, messages in reads if first_write is None or index < first_write
        ]
        baseline_value = (
            baseline_candidates[0][1] if baseline_candidates else _synthetic_discord_messages(seed_messages)
        )
        last_write = writes[-1].call_index if writes else None
        final_candidates = [(index, messages) for index, messages in reads if last_write is None or index > last_write]
        if final_candidates:
            final_value = final_candidates[-1][1]
        else:
            final_value = list(baseline_value)
            for call in writes:
                response = call.response_body
                if not isinstance(response, dict):
                    raise StateCaptureError("Discord mutation has no final read or message response")
                final_value.append(
                    _string_object(
                        cast(dict[object, object], response),
                        label="Discord message response",
                    )
                )
        _verify_seed_message_contents(baseline_value, seed_messages)
        expected_final_contents = [
            *_discord_message_contents(seed_messages, label="Discord seed messages"),
            *(_discord_write_content(call) for call in writes),
        ]
        if sorted(_discord_message_contents(final_value, label="Discord final messages")) != sorted(
            expected_final_contents
        ):
            raise StateCaptureError("Discord final messages contradict the successful call evidence")
        baseline_messages[channel_id] = baseline_value
        final_messages[channel_id] = final_value

    _verify_discord_admin(baseline, query_guilds, channels_by_key, baseline_messages)
    _verify_discord_admin(final, final_query_guilds, channels_by_key, final_messages)
    return (
        _inject_discord(
            baseline,
            baseline_capture.query_id,
            query_guilds,
            channels_by_key,
            baseline_messages,
        ),
        _inject_discord(
            final,
            final_capture.query_id,
            final_query_guilds,
            channels_by_key,
            final_messages,
        ),
    )


def _seed_discord(seed: JsonValue) -> dict[str, dict[str, Any]]:
    root = _string_object(seed, label="Discord seed")
    raw_guilds = root.get("guilds")
    if not isinstance(raw_guilds, list):
        raise StateCaptureError("Discord seed has no guilds")
    result: dict[str, dict[str, Any]] = {}
    for raw_guild in cast(list[object], raw_guilds):
        guild = _string_object(raw_guild, label="Discord seed guild")
        name = guild.get("name")
        raw_channels = guild.get("channels")
        if not isinstance(name, str) or not isinstance(raw_channels, list) or name in result:
            raise StateCaptureError("Discord seed guild is invalid or duplicated")
        channels: dict[str, list[dict[str, Any]]] = {}
        for raw_channel in cast(list[object], raw_channels):
            channel = _string_object(raw_channel, label="Discord seed channel")
            channel_name = channel.get("name")
            raw_messages = channel.get("messages", [])
            if not isinstance(channel_name, str) or not isinstance(raw_messages, list) or channel_name in channels:
                raise StateCaptureError("Discord seed channel is invalid or duplicated")
            channels[channel_name] = [
                _string_object(message, label="Discord seed message") for message in cast(list[object], raw_messages)
            ]
        result[name] = {"channels": channels}
    return result


def _discord_query_guilds(capture: CapturedQueryState) -> dict[str, dict[str, Any]]:
    raw_guilds = capture.body.get("guilds") if isinstance(capture.body, dict) else capture.body
    guilds = _object_array(raw_guilds, label="Discord guild query")
    result: dict[str, dict[str, Any]] = {}
    for guild in guilds:
        name = guild.get("name")
        guild_id = guild.get("id")
        if not isinstance(name, str) or not isinstance(guild_id, str) or name in result:
            raise StateCaptureError("Discord guild query has invalid or duplicate identity")
        result[name] = guild
    return result


def _synthetic_discord_messages(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    occurrences: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            raise StateCaptureError("Discord seed message has no content")
        occurrence = occurrences.get(content, 0)
        occurrences[content] = occurrence + 1
        result.append(
            {
                "id": _content_identity(content, occurrence),
                "content": content,
                "author": {"username": "twin-bot"},
            }
        )
    return result


def _discord_html_messages(
    body: str,
) -> tuple[str, str, list[dict[str, Any]]] | None:
    channel_id_match = re.search(
        r"<span class=['\"]channel-header-id['\"]>([^<]+)</span>",
        body,
    )
    channel_name_match = re.search(
        r"<span class=['\"]channel-header-name['\"]>([^<]+)</span>",
        body,
    )
    if channel_id_match is None or channel_name_match is None:
        return None
    contents = [
        html.unescape(value) for value in re.findall(r"<div class=['\"]msg-text['\"]>(.*?)</div>", body, re.DOTALL)
    ]
    occurrences: dict[str, int] = {}
    messages: list[dict[str, Any]] = []
    for content in contents:
        occurrence = occurrences.get(content, 0)
        occurrences[content] = occurrence + 1
        messages.append(
            {
                "id": _content_identity(content, occurrence),
                "content": content,
                "author": {"username": "twin-bot"},
            }
        )
    return (
        html.unescape(channel_id_match.group(1)),
        html.unescape(channel_name_match.group(1)),
        messages,
    )


def _content_identity(content: str, occurrence: int) -> str:
    digest = hashlib.sha256(content.encode()).hexdigest()[:24]
    return f"recovered-{digest}-{occurrence}"


def _verify_seed_message_contents(
    messages: Sequence[Mapping[str, Any]],
    seed_messages: Sequence[Mapping[str, Any]],
) -> None:
    actual = sorted(_discord_message_contents(messages, label="Discord baseline messages"))
    expected = sorted(_discord_message_contents(seed_messages, label="Discord seed messages"))
    if actual != expected:
        raise StateCaptureError("Discord pre-mutation messages differ from the hash-bound seed")


def _discord_message_contents(
    messages: Sequence[Mapping[str, Any]],
    *,
    label: str,
) -> list[str]:
    result: list[str] = []
    seen_ids: set[str] = set()
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            raise StateCaptureError(f"{label} contain a message without string content")
        message_id = message.get("id")
        if message_id is not None:
            if not isinstance(message_id, str) or message_id in seen_ids:
                raise StateCaptureError(f"{label} contain an invalid or duplicate message ID")
            seen_ids.add(message_id)
        result.append(content)
    return result


def _discord_write_content(call: RecordedProviderCall) -> str:
    body = _string_object(call.arguments.get("body"), label="Discord message arguments")
    content = body.get("content")
    if not isinstance(content, str):
        raise StateCaptureError("Discord message mutation has no string content")
    response = call.response_body
    if isinstance(response, dict):
        response_mapping = cast(dict[object, object], response)
        response_content = response_mapping.get("content")
        if response_content is not None and response_content != content:
            raise StateCaptureError("Discord message response contradicts its request content")
    return content


def _verify_discord_admin(
    snapshot: TrustedStateSnapshot,
    guilds_by_name: Mapping[str, Mapping[str, Any]],
    channels_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    messages_by_channel: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    provider = snapshot.providers.get("discord")
    if provider is None:
        raise StateCaptureError("Discord snapshot has no trusted admin state")
    raw_guilds = provider.state.get("guilds")
    if not isinstance(raw_guilds, dict):
        raise StateCaptureError("Discord trusted admin state has no guild map")
    admin_guilds = cast(dict[object, object], raw_guilds)
    expected_guild_ids = {
        cast(str, guild["id"]) for guild in guilds_by_name.values() if isinstance(guild.get("id"), str)
    }
    if set(admin_guilds) != expected_guild_ids:
        raise StateCaptureError("Discord trusted guild state differs from the recovered guilds")
    for guild_name, guild in guilds_by_name.items():
        guild_id = cast(str, guild["id"])
        admin_guild = _string_object(admin_guilds[guild_id], label="Discord trusted guild")
        if admin_guild.get("name") != guild_name:
            raise StateCaptureError("Discord trusted guild name differs from the recovered guild")
    channel_count = provider.state.get("channels")
    if isinstance(channel_count, bool) or not isinstance(channel_count, int) or channel_count != len(channels_by_key):
        raise StateCaptureError("Discord trusted channel count differs from recovered channels")
    message_count = provider.state.get("messages")
    expected = sum(len(messages) for messages in messages_by_channel.values())
    if isinstance(message_count, bool) or not isinstance(message_count, int) or message_count != expected:
        raise StateCaptureError("Discord trusted message count differs from recovered messages")


def _inject_discord(
    snapshot: TrustedStateSnapshot,
    query_id: str,
    guilds_by_name: Mapping[str, Mapping[str, Any]],
    channels_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    messages_by_channel: Mapping[str, Sequence[Mapping[str, Any]]],
) -> TrustedStateSnapshot:
    guilds: list[JsonValue] = []
    for guild_name, raw_guild in sorted(guilds_by_name.items()):
        guild_id = raw_guild.get("id")
        if not isinstance(guild_id, str):
            raise StateCaptureError("Discord recovered guild has no ID")
        channels: list[JsonValue] = []
        guild_channels = sorted(
            (
                channel_name,
                raw_channel,
            )
            for (channel_guild_id, channel_name), raw_channel in channels_by_key.items()
            if channel_guild_id == guild_id
        )
        for channel_name, raw_channel in guild_channels:
            channel_id = raw_channel.get("id")
            if not isinstance(channel_id, str):
                raise StateCaptureError("Discord recovered channel has no ID")
            channels.append(
                cast(
                    JsonValue,
                    {
                        **raw_channel,
                        "name": channel_name,
                        "messages": list(messages_by_channel[channel_id]),
                    },
                )
            )
        guilds.append(
            cast(
                JsonValue,
                {
                    **raw_guild,
                    "name": guild_name,
                    "channels": channels,
                },
            )
        )
    queries = dict(snapshot.queries)
    queries[query_id] = replace(queries[query_id], body=guilds)
    return TrustedStateSnapshot(providers=dict(snapshot.providers), queries=queries)


def _object_array(value: object, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise StateCaptureError(f"{label} must be an array")
    return [_string_object(item, label=f"{label}[{index}]") for index, item in enumerate(cast(list[object], value))]


def _string_object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StateCaptureError(f"{label} must be an object with string keys")
    mapping = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise StateCaptureError(f"{label} must be an object with string keys")
    return cast(dict[str, Any], mapping)
