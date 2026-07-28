# pyright: reportPrivateUsage=false

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from arga_twins_benchmark.evaluation.protocol import JsonValue
from arga_twins_benchmark.evaluation.state_capture import (
    CapturedProviderState,
    CapturedQueryState,
    StateCaptureError,
    TrustedStateSnapshot,
)
from arga_twins_benchmark.reporting import preserved_snapshot_recovery as recovery


def _query(
    *,
    query_id: str,
    provider: str,
    role: str,
    canonicalizer: str,
    body: Any,
) -> CapturedQueryState:
    return CapturedQueryState(
        query_id=query_id,
        provider_name=provider,
        provider_role=role,
        method="GET",
        path="/snapshot",
        canonicalizer=canonicalizer,
        status_code=200,
        body=body,
    )


def _json(value: object) -> JsonValue:
    return cast(JsonValue, value)


def _call(
    index: int,
    *,
    provider: str,
    role: str,
    method: str,
    path: str,
    body: object,
    arguments: dict[str, Any] | None = None,
    status_code: int = 200,
    truncated: bool = False,
) -> recovery.RecordedProviderCall:
    return recovery.RecordedProviderCall(
        call_index=index,
        provider=provider,
        requested_provider=role,
        method=method,
        path=path,
        status_code=status_code,
        arguments=arguments or {"provider": role, "method": method, "path": path},
        response_body=body,
        truncated=truncated,
    )


def _notion_page(page_id: str, title: str) -> dict[str, Any]:
    return {
        "id": page_id,
        "properties": {
            "Name": {
                "title": [{"plain_text": title}],
            }
        },
    }


def _notion_blocks(text: str, *, has_more: bool = False) -> dict[str, Any]:
    return {
        "has_more": has_more,
        "next_cursor": "next" if has_more else None,
        "results": [
            {
                "id": f"block-{text}",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"plain_text": text}]},
            }
        ],
    }


def _discord_snapshot(*, messages: int) -> TrustedStateSnapshot:
    guild = {"id": "guild-1", "name": "Acme Ops"}
    return TrustedStateSnapshot(
        providers={
            "discord": CapturedProviderState(
                provider_name="discord",
                provider_role="team_chat",
                state=cast(
                    dict[str, JsonValue],
                    {
                        "guilds": {"guild-1": guild},
                        "channels": 1,
                        "messages": messages,
                    },
                ),
            )
        },
        queries={
            "discord": _query(
                query_id="discord",
                provider="discord",
                role="team_chat",
                canonicalizer="discord_guild_channels_messages_v1",
                body=[guild],
            )
        },
    )


def test_drive_marker_recovery_strips_sentence_punctuation_and_rejects_conflict() -> None:
    content = "Evidence package. Content Marker: SOC2-Q2-311. Classification: External-approved."
    checksum = hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()
    snapshot = TrustedStateSnapshot(
        providers={"google_drive": CapturedProviderState("google_drive", "storage", {"files": 1})},
        queries={
            "drive": _query(
                query_id="drive",
                provider="google_drive",
                role="storage",
                canonicalizer="drive_files_content_hash_v1",
                body={
                    "files": [
                        {
                            "id": "file-1",
                            "name": "Q2 SOC2 Evidence.txt",
                            "md5Checksum": checksum.upper(),
                        }
                    ]
                },
            )
        },
    )
    seed = _json(
        {
            "files": [
                {
                    "name": "Q2 SOC2 Evidence.txt",
                    "content": content,
                }
            ]
        }
    )

    recovered = recovery._recover_drive_markers(snapshot, seed)

    assert recovered.queries["drive"].body["files"][0]["content_marker"] == "SOC2-Q2-311"  # type: ignore[index]

    query = snapshot.queries["drive"]
    conflicting = TrustedStateSnapshot(
        providers=snapshot.providers,
        queries={
            "drive": replace(
                query,
                body={
                    "files": [
                        {
                            "id": "file-1",
                            "name": "Q2 SOC2 Evidence.txt",
                            "md5Checksum": checksum,
                            "content_marker": "FORGED",
                        }
                    ]
                },
            )
        },
    )
    with pytest.raises(StateCaptureError, match="contradicts"):
        recovery._recover_drive_markers(conflicting, seed)


def test_notion_recovery_binds_seed_and_complete_pre_and_post_reads() -> None:
    page = _notion_page("page-1", "Production Runbook")
    snapshot = TrustedStateSnapshot(
        providers={"notion": CapturedProviderState("notion", "knowledge_base", {"pages": 1})},
        queries={
            "notion": _query(
                query_id="notion",
                provider="notion",
                role="knowledge_base",
                canonicalizer="notion_pages_markdown_v1",
                body={"results": [page]},
            )
        },
    )
    calls = [
        _call(
            1,
            provider="notion",
            role="knowledge_base",
            method="GET",
            path="/v1/blocks/page-1/children?page_size=100",
            body=_notion_blocks("Old"),
        ),
        _call(
            2,
            provider="notion",
            role="knowledge_base",
            method="PATCH",
            path="/v1/blocks/page-1/children",
            arguments={
                "provider": "knowledge_base",
                "method": "PATCH",
                "path": "/v1/blocks/page-1/children",
                "body": {"children": []},
            },
            body={"results": []},
        ),
        _call(
            3,
            provider="notion",
            role="knowledge_base",
            method="GET",
            path="/v1/blocks/page-1/children?page_size=100",
            body=_notion_blocks("New"),
        ),
    ]

    baseline, final = recovery._recover_notion_markdown(
        baseline=snapshot,
        final=snapshot,
        seed=_json({"pages": [{"title": "Production Runbook", "content": "Old\n"}]}),
        calls=calls,
    )

    assert baseline.queries["notion"].body["results"][0]["markdown"] == "Old\n"  # type: ignore[index]
    assert final.queries["notion"].body["results"][0]["markdown"] == "New\n"  # type: ignore[index]


def test_notion_recovery_rejects_incomplete_reads_and_unbound_mutations() -> None:
    page = _notion_page("page-1", "Production Runbook")
    snapshot = TrustedStateSnapshot(
        providers={"notion": CapturedProviderState("notion", "knowledge_base", {"pages": 1})},
        queries={
            "notion": _query(
                query_id="notion",
                provider="notion",
                role="knowledge_base",
                canonicalizer="notion_pages_markdown_v1",
                body={"results": [page]},
            )
        },
    )
    seed = _json({"pages": [{"title": "Production Runbook", "content": "Old\n"}]})

    with pytest.raises(StateCaptureError, match="incomplete"):
        recovery._recover_notion_markdown(
            baseline=snapshot,
            final=snapshot,
            seed=seed,
            calls=[
                _call(
                    1,
                    provider="notion",
                    role="knowledge_base",
                    method="GET",
                    path="/v1/blocks/page-1/children",
                    body=_notion_blocks("Old", has_more=True),
                )
            ],
        )

    with pytest.raises(StateCaptureError, match="cannot be bound"):
        recovery._recover_notion_markdown(
            baseline=snapshot,
            final=snapshot,
            seed=seed,
            calls=[
                _call(
                    1,
                    provider="notion",
                    role="knowledge_base",
                    method="POST",
                    path="/v1/comments",
                    arguments={
                        "provider": "knowledge_base",
                        "method": "POST",
                        "path": "/v1/comments",
                        "body": {"text": "side effect"},
                    },
                    body={"id": "comment-1"},
                )
            ],
        )


def test_discord_recovery_uses_seed_for_missing_pre_read_and_query_string_final_read() -> None:
    baseline = _discord_snapshot(messages=0)
    final = _discord_snapshot(messages=1)
    message = {"id": "message-1", "content": "TRACKED INC-420 AS OPS-9: timeout."}
    calls = [
        _call(
            1,
            provider="discord",
            role="team_chat",
            method="GET",
            path="/api/v10/guilds/guild-1/channels",
            body=[{"id": "channel-1", "guild_id": "guild-1", "name": "incidents"}],
        ),
        _call(
            2,
            provider="discord",
            role="team_chat",
            method="POST",
            path="/api/v10/channels/channel-1/messages",
            arguments={
                "provider": "team_chat",
                "method": "POST",
                "path": "/api/v10/channels/channel-1/messages",
                "body": {"content": message["content"]},
            },
            body=message,
        ),
        _call(
            3,
            provider="discord",
            role="team_chat",
            method="GET",
            path="/api/v10/channels/channel-1/messages?limit=50",
            body=[message],
        ),
    ]

    recovered_baseline, recovered_final = recovery._recover_discord(
        baseline=baseline,
        final=final,
        seed=_json(
            {
                "guilds": [
                    {
                        "name": "Acme Ops",
                        "channels": [
                            {
                                "name": "incidents",
                                "messages": cast(list[JsonValue], []),
                            }
                        ],
                    }
                ]
            }
        ),
        calls=calls,
    )

    assert recovered_baseline.queries["discord"].body[0]["channels"][0]["messages"] == []  # type: ignore[index]
    assert recovered_final.queries["discord"].body[0]["channels"][0]["messages"] == [message]  # type: ignore[index]


def test_discord_recovery_rejects_unrecognized_successful_mutation() -> None:
    snapshot = _discord_snapshot(messages=0)
    calls = [
        _call(
            1,
            provider="discord",
            role="team_chat",
            method="GET",
            path="/api/v10/guilds/guild-1/channels",
            body=[{"id": "channel-1", "guild_id": "guild-1", "name": "incidents"}],
        ),
        _call(
            2,
            provider="discord",
            role="team_chat",
            method="PATCH",
            path="/api/v10/channels/channel-1",
            arguments={
                "provider": "team_chat",
                "method": "PATCH",
                "path": "/api/v10/channels/channel-1",
                "body": {"name": "renamed"},
            },
            body={"id": "channel-1", "name": "renamed"},
        ),
    ]

    with pytest.raises(StateCaptureError, match="unsupported successful Discord mutation"):
        recovery._recover_discord(
            baseline=snapshot,
            final=snapshot,
            seed=_json(
                {
                    "guilds": [
                        {
                            "name": "Acme Ops",
                            "channels": [
                                {
                                    "name": "incidents",
                                    "messages": cast(list[JsonValue], []),
                                }
                            ],
                        }
                    ]
                }
            ),
            calls=calls,
        )


def test_recorded_calls_accept_pre_http_failure_but_reject_trace_mismatch() -> None:
    embedded: dict[str, Any] = {
        "sequence": 1,
        "requested_provider": "source_tracker",
        "provider": "jira",
        "method": None,
        "path": "/issue/MIG-33",
        "status_code": None,
        "operation": None,
        "operation_type": None,
        "error": "method must be one of: DELETE, GET, PATCH, POST, PUT",
        "truncated": False,
    }
    invocation: dict[str, Any] = {
        "events": [
            {
                "type": "tool_call",
                "provider_call_index": 8,
                "arguments": {"provider": "source_tracker", "path": "/issue/MIG-33"},
                "output": {
                    **embedded,
                    "trace": embedded,
                    "body": None,
                },
            }
        ]
    }
    trace: dict[str, Any] = {"events": [embedded]}

    assert recovery._recorded_provider_calls(invocation, trace) == []

    original_event = cast(dict[str, Any], cast(list[object], invocation["events"])[0])
    original_output = cast(dict[str, Any], original_event["output"])
    mismatched_invocation: dict[str, Any] = {
        "events": [
            {
                **original_event,
                "output": {**original_output, "path": "/different"},
            }
        ]
    }
    with pytest.raises(StateCaptureError, match="invocation output and provider trace differ"):
        recovery._recorded_provider_calls(mismatched_invocation, trace)


def test_public_recovery_rejects_seed_path_escape(tmp_path: Path) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    instance_path = instance_dir / "instance.yaml"
    instance_path.write_text("id: fixture\n")
    empty = TrustedStateSnapshot(providers={})

    with pytest.raises(StateCaptureError, match="escapes"):
        recovery.recover_preserved_trial_snapshots(
            baseline=empty,
            final=empty,
            invocation={"events": []},
            trace_payload={"events": []},
            control_payload={
                "instance_id": "fixture",
                "scenario_content_sha256": "hash",
            },
            instance_id="fixture",
            instance_path=instance_path,
            seed_files={"discord": "../outside.json"},
            expected_episode_hash="hash",
        )
