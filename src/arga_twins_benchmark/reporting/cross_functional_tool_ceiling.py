from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

LEGACY_PROVIDER_TOOL_LIMIT = 100
LEGACY_OFFICIAL_DOCS_TOOL_LIMIT = 20
LEGACY_TOTAL_TOOL_LIMIT = LEGACY_PROVIDER_TOOL_LIMIT + LEGACY_OFFICIAL_DOCS_TOOL_LIMIT

_PROMPT_PROTOCOL = "arga-bench-trial-prompt/1"
_PROVIDER_TRACE_PROTOCOL = "arga-bench-provider-trace/1"
_DOCS_TRACE_PROTOCOL = "arga-bench-official-docs-trace/1"
_TOOL_STEPS_PROTOCOL = "arga-bench-tool-steps/1"
_TOOL_CALL_EVENT_TYPE = "tool_call"
_LIMITS = {
    "provider_api": (
        LEGACY_PROVIDER_TOOL_LIMIT,
        f"provider_api call limit of {LEGACY_PROVIDER_TOOL_LIMIT} has been reached",
    ),
    "provider_docs": (
        LEGACY_OFFICIAL_DOCS_TOOL_LIMIT,
        f"provider_docs call limit of {LEGACY_OFFICIAL_DOCS_TOOL_LIMIT} has been reached",
    ),
}


def _object_list(value: object) -> list[Mapping[str, Any]] | None:
    if not isinstance(value, list):
        return None
    items = cast(list[object], value)
    if not all(isinstance(item, dict) for item in items):
        return None
    return [cast(Mapping[str, Any], item) for item in items]


def _sequences_are_complete(items: Sequence[Mapping[str, Any]]) -> bool:
    return [item.get("sequence") for item in items] == list(range(1, len(items) + 1))


def legacy_gateway_ceiling_rejections(
    *,
    prompt: Mapping[str, Any],
    invocation: Mapping[str, Any],
    provider_trace: Mapping[str, Any],
    docs_trace: Mapping[str, Any],
    tool_steps: Mapping[str, Any],
) -> frozenset[str]:
    """Return old gateway ceilings proven by complete, mutually consistent records.

    A rejection is eligible only when the candidate was actually run with the
    historical 100-provider/20-doc split and the exact rejection is present in
    the gateway trace, normalized tool steps, and mediated model invocation.
    Merely repeating a limit string in model text or an unrelated artifact is
    deliberately insufficient.
    """

    config = invocation.get("config")
    if (
        prompt.get("protocol") != _PROMPT_PROTOCOL
        or prompt.get("provider_tool_call_limit") != LEGACY_PROVIDER_TOOL_LIMIT
        or prompt.get("official_docs_tool_call_limit") != LEGACY_OFFICIAL_DOCS_TOOL_LIMIT
        or invocation.get("status") != "completed"
        or not isinstance(config, dict)
        or cast(dict[str, Any], config).get("max_tool_calls") != LEGACY_TOTAL_TOOL_LIMIT
        or provider_trace.get("protocol") != _PROVIDER_TRACE_PROTOCOL
        or docs_trace.get("protocol") != _DOCS_TRACE_PROTOCOL
        or tool_steps.get("protocol") != _TOOL_STEPS_PROTOCOL
    ):
        return frozenset()

    provider_events = _object_list(provider_trace.get("events"))
    docs_events = _object_list(docs_trace.get("events"))
    steps = _object_list(tool_steps.get("steps"))
    invocation_events = _object_list(invocation.get("events"))
    invocation_tool_calls = invocation.get("tool_calls")
    if (
        provider_events is None
        or docs_events is None
        or steps is None
        or invocation_events is None
        or isinstance(invocation_tool_calls, bool)
        or not isinstance(invocation_tool_calls, int)
        or invocation_tool_calls != len(steps)
        or not _sequences_are_complete(provider_events)
        or not _sequences_are_complete(docs_events)
        or not _sequences_are_complete(steps)
    ):
        return frozenset()

    model_tool_calls = [event for event in invocation_events if event.get("type") == _TOOL_CALL_EVENT_TYPE]
    if len(model_tool_calls) != invocation_tool_calls:
        return frozenset()
    step_kinds = [step.get("kind") for step in steps]
    if [event.get("name") for event in model_tool_calls] != step_kinds:
        return frozenset()
    if sum(kind == "provider_api" for kind in step_kinds) != len(provider_events) or sum(
        kind == "provider_docs" for kind in step_kinds
    ) != len(docs_events):
        return frozenset()

    trace_by_kind = {
        "provider_api": provider_events,
        "provider_docs": docs_events,
    }
    rejections: set[str] = set()
    for kind, (limit, message) in _LIMITS.items():
        trace_matches = [event for event in trace_by_kind[kind] if event.get("error") == message]
        if not trace_matches:
            continue
        trace_sequences = [event.get("sequence") for event in trace_matches]
        step_sequences = [
            ordinal
            for ordinal, step in enumerate((step for step in steps if step.get("kind") == kind), start=1)
            if step.get("error") == message
        ]
        invocation_sequences = [
            ordinal
            for ordinal, event in enumerate(
                (event for event in model_tool_calls if event.get("name") == kind),
                start=1,
            )
            if isinstance(event.get("output"), dict)
            and cast(dict[str, Any], event["output"]).get("error") == message
        ]
        if (
            trace_sequences[0] != limit + 1
            or not all(isinstance(sequence, int) and sequence > limit for sequence in trace_sequences)
            or trace_sequences != step_sequences
            or trace_sequences != invocation_sequences
        ):
            return frozenset()
        rejections.add(kind)
    return frozenset(rejections)
