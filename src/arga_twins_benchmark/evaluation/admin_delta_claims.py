from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from arga_twins_benchmark.evaluation.protocol import JsonValue, Mutation
from arga_twins_benchmark.evaluation.state_capture import RawStateDelta, StateCaptureError


class AdminDeltaClaimError(StateCaptureError):
    """Raised when provider bookkeeping cannot be tied to exact entity evidence."""


@dataclass(frozen=True)
class AdminDeltaClaims:
    claimed_indices: frozenset[int]
    synthetic_mutations: tuple[Mutation, ...] = ()


def claim_provider_admin_deltas(
    *,
    raw_deltas: Sequence[RawStateDelta],
    canonical_mutations: Sequence[Mutation],
) -> AdminDeltaClaims:
    """Claim exact provider-native mutation bundles.

    Provider twins commonly expose one semantic write as an entity delta plus
    counters, journals, clocks, or denormalized projections. A bundle is
    accepted only when every provider-specific invariant reconciles with the
    canonical entity mutations. Unknown paths are deliberately left unclaimed.
    """

    grouped: dict[str, list[tuple[int, RawStateDelta]]] = defaultdict(list)
    for index, delta in enumerate(raw_deltas):
        if delta.scope == "admin":
            grouped[delta.provider_name].append((index, delta))

    claimed: set[int] = set()
    synthetic: list[Mutation] = []
    for provider_name, entries in sorted(grouped.items()):
        provider_mutations = [
            mutation
            for mutation in canonical_mutations
            if any(delta.provider_role == mutation.twin for _, delta in entries)
        ]
        resolution = _provider_claims(
            provider_name=provider_name,
            entries=entries,
            mutations=provider_mutations,
        )
        overlap = claimed & set(resolution.claimed_indices)
        if overlap:
            raise AdminDeltaClaimError(f"provider admin deltas were claimed more than once: {sorted(overlap)}")
        claimed.update(resolution.claimed_indices)
        synthetic.extend(resolution.synthetic_mutations)
    return AdminDeltaClaims(frozenset(claimed), tuple(synthetic))


def _provider_claims(
    *,
    provider_name: str,
    entries: Sequence[tuple[int, RawStateDelta]],
    mutations: Sequence[Mutation],
) -> AdminDeltaClaims:
    if provider_name == "slack":
        return _claim_slack(entries, mutations)
    if provider_name == "discord":
        return _claim_discord(entries, mutations)
    if provider_name == "github":
        return _claim_github(entries, mutations)
    if provider_name == "jira":
        return _claim_jira(entries, mutations)
    if provider_name == "linear":
        return _claim_linear(entries, mutations)
    if provider_name == "google_drive":
        return _claim_drive(entries, mutations)
    if provider_name == "google_calendar":
        return _claim_calendar(entries, mutations)
    if provider_name == "gmail":
        return _claim_gmail(entries, mutations)
    if provider_name == "notion":
        return _claim_notion(entries, mutations)
    if provider_name == "stripe":
        return _claim_stripe(entries, mutations)
    return AdminDeltaClaims(frozenset())


def _claim_slack(
    entries: Sequence[tuple[int, RawStateDelta]],
    mutations: Sequence[Mutation],
) -> AdminDeltaClaims:
    message_creates = [
        mutation for mutation in mutations if mutation.resource_type == "message" and mutation.operation == "create"
    ]
    claimed: set[int] = set()
    event_messages: list[tuple[int, str, str]] = []
    channel_counts: dict[str, int] = {}
    for index, delta in entries:
        if delta.path == ("logical_now",):
            _require_increasing_timestamp(delta, label="Slack logical_now")
            claimed.add(index)
            continue
        if len(delta.path) == 2 and delta.path[0] == "events" and delta.operation == "create":
            event = _object(delta.after, label="Slack message event")
            envelope = _object(event.get("envelope"), label="Slack message event envelope")
            message = _object(envelope.get("event"), label="Slack message event payload")
            if (
                message.get("type") != "message"
                or not isinstance(message.get("channel"), str)
                or not isinstance(message.get("ts"), str)
                or not isinstance(message.get("text"), str)
                or not isinstance(message.get("user"), str)
            ):
                raise AdminDeltaClaimError("Slack event is not a complete message event")
            channel_id = cast(str, message["channel"])
            message_id = cast(str, message["ts"])
            matches = [
                mutation
                for mutation in message_creates
                if mutation.resource_id == f"{channel_id}:{message_id}"
                and isinstance(mutation.after, dict)
                and mutation.after.get("channel_id") == channel_id
                and mutation.after.get("text") == message["text"]
                and mutation.after.get("author") == message["user"]
            ]
            if len(matches) != 1:
                raise AdminDeltaClaimError("Slack message event has no unique canonical message")
            event_messages.append((index, channel_id, message_id))
            claimed.add(index)
            continue
        if len(delta.path) == 3 and delta.path[0] == "channels" and delta.path[2] == "message_count":
            channel_id = _path_identity(delta.path[1], "id")
            increment = _integer_delta(delta, label="Slack channel message_count")
            channel_counts[channel_id] = increment
            claimed.add(index)

    created_by_channel: dict[str, int] = defaultdict(int)
    for _, channel_id, _ in event_messages:
        created_by_channel[channel_id] += 1
    if channel_counts != dict(created_by_channel):
        raise AdminDeltaClaimError("Slack channel counters do not equal created message events")
    if len(event_messages) != len(message_creates):
        raise AdminDeltaClaimError("Slack message events do not cover canonical message creates")
    return AdminDeltaClaims(frozenset(claimed))


def _claim_discord(
    entries: Sequence[tuple[int, RawStateDelta]],
    mutations: Sequence[Mutation],
) -> AdminDeltaClaims:
    recognized = [(index, delta) for index, delta in entries if delta.path in {("messages",), ("events",)}]
    if not recognized:
        return AdminDeltaClaims(frozenset())
    if len(recognized) != 2 or {delta.path for _, delta in recognized} != {
        ("messages",),
        ("events",),
    }:
        raise AdminDeltaClaimError("Discord message mutation counters are incomplete")
    creates = [
        mutation for mutation in mutations if mutation.resource_type == "message" and mutation.operation == "create"
    ]
    for _, delta in recognized:
        if _integer_delta(delta, label=f"Discord {delta.path[0]}") != len(creates):
            raise AdminDeltaClaimError("Discord counters do not equal canonical message creates")
    return AdminDeltaClaims(frozenset(index for index, _ in recognized))


def _claim_github(
    entries: Sequence[tuple[int, RawStateDelta]],
    mutations: Sequence[Mutation],
) -> AdminDeltaClaims:
    claimed: set[int] = set()
    review_creates = [
        mutation
        for mutation in mutations
        if mutation.resource_type == "pull_request_review" and mutation.operation == "create"
    ]
    review_deletes = [
        mutation
        for mutation in mutations
        if mutation.resource_type == "pull_request_review" and mutation.operation == "delete"
    ]
    for index, delta in entries:
        if delta.path != ("summary", "repos"):
            continue
        before = _index_objects(delta.before, key="full_name", label="GitHub summary before")
        after = _index_objects(delta.after, key="full_name", label="GitHub summary after")
        if set(before) != set(after):
            raise AdminDeltaClaimError("GitHub repository summary identity set changed")
        for repository in sorted(before):
            old = before[repository]
            new = after[repository]
            changed = {key for key in set(old) | set(new) if old.get(key) != new.get(key)}
            if changed - {"reviews"}:
                raise AdminDeltaClaimError("GitHub repository summary changed outside reviews")
            expected = sum(_mutation_repository(mutation) == repository for mutation in review_creates) - sum(
                _mutation_repository(mutation) == repository for mutation in review_deletes
            )
            actual = _numeric_difference(old.get("reviews"), new.get("reviews"), label="GitHub reviews")
            if actual != expected:
                raise AdminDeltaClaimError("GitHub review count does not match canonical reviews")
        claimed.add(index)
    return AdminDeltaClaims(frozenset(claimed))


def _claim_jira(
    entries: Sequence[tuple[int, RawStateDelta]],
    mutations: Sequence[Mutation],
) -> AdminDeltaClaims:
    issues = [mutation for mutation in mutations if mutation.resource_type == "issue"]
    recognized = [
        (index, delta)
        for index, delta in entries
        if delta.path == ("counts", "issues")
        or (len(delta.path) == 3 and delta.path[0] == "projects" and delta.path[2] == "issues")
    ]
    if not recognized:
        return AdminDeltaClaims(frozenset())
    global_entries = [(index, delta) for index, delta in recognized if delta.path == ("counts", "issues")]
    project_entries = [(index, delta) for index, delta in recognized if delta.path != ("counts", "issues")]
    if len(global_entries) != 1 or not project_entries:
        raise AdminDeltaClaimError("Jira issue counter bundle is incomplete")
    expected_global = _net_count(issues)
    if _integer_delta(global_entries[0][1], label="Jira issue count") != expected_global:
        raise AdminDeltaClaimError("Jira global issue count does not match canonical issues")
    for _, delta in project_entries:
        project_key = _path_identity(delta.path[1], "key")
        scoped = [mutation for mutation in issues if _mutation_field(mutation, "project_key") == project_key]
        if _integer_delta(delta, label=f"Jira project {project_key} issue count") != _net_count(scoped):
            raise AdminDeltaClaimError("Jira project issue count does not match canonical issues")
    if sum(_integer_delta(delta, label="Jira project issue count") for _, delta in project_entries) != expected_global:
        raise AdminDeltaClaimError("Jira project counters do not sum to the global issue count")
    return AdminDeltaClaims(frozenset(index for index, _ in recognized))


def _claim_linear(
    entries: Sequence[tuple[int, RawStateDelta]],
    mutations: Sequence[Mutation],
) -> AdminDeltaClaims:
    claimed: set[int] = set()
    entity_mutations = [mutation for mutation in mutations if mutation.resource_type in {"issue", "issue_comment"}]
    for index, delta in entries:
        if delta.path == ("logical_now",):
            _require_increasing_timestamp(delta, label="Linear logical_now")
            claimed.add(index)
        elif delta.path == ("sync_id",):
            if _integer_delta(delta, label="Linear sync_id") != len(entity_mutations):
                raise AdminDeltaClaimError("Linear sync_id does not equal changed entity records")
            claimed.add(index)
        elif delta.path in {("counts", "issues"), ("counts", "comments")}:
            resource_type = "issue" if delta.path[-1] == "issues" else "issue_comment"
            scoped = [mutation for mutation in entity_mutations if mutation.resource_type == resource_type]
            if _integer_delta(delta, label=f"Linear {delta.path[-1]} count") != _net_count(scoped):
                raise AdminDeltaClaimError("Linear entity counter does not match canonical mutations")
            claimed.add(index)
        elif len(delta.path) == 3 and delta.path[0] == "teams" and delta.path[2] == "issue_counter":
            team_id = _path_identity(delta.path[1], "id")
            creates = [
                mutation
                for mutation in entity_mutations
                if mutation.resource_type == "issue"
                and mutation.operation == "create"
                and _mutation_field(mutation, "team_id") == team_id
            ]
            increment = _integer_delta(delta, label="Linear team issue_counter")
            if increment != len(creates):
                raise AdminDeltaClaimError("Linear team counter does not match issue creates")
            before = _integer(delta.before, label="Linear team counter before")
            created_numbers = sorted(
                number
                for mutation in creates
                if isinstance((number := _mutation_field(mutation, "number")), int) and not isinstance(number, bool)
            )
            if created_numbers != list(range(before + 1, before + increment + 1)):
                raise AdminDeltaClaimError("Linear generated issue numbers do not match team counter")
            claimed.add(index)
    return AdminDeltaClaims(frozenset(claimed))


def _claim_drive(
    entries: Sequence[tuple[int, RawStateDelta]],
    mutations: Sequence[Mutation],
) -> AdminDeltaClaims:
    permission_deltas = [
        (index, delta)
        for index, delta in entries
        if len(delta.path) == 4
        and delta.path[0] == "files"
        and delta.path[2] == "permissions"
        and delta.operation == "create"
    ]
    if not permission_deltas:
        return AdminDeltaClaims(frozenset())
    claimed: set[int] = set()
    permission_creates = [
        mutation
        for mutation in mutations
        if mutation.resource_type == "file_permission" and mutation.operation == "create"
    ]
    if len(permission_deltas) != len(permission_creates):
        raise AdminDeltaClaimError("Drive permission objects do not match canonical creates")
    expected_ids: dict[str, str] = {}
    for index, delta in permission_deltas:
        file_id = _path_identity(delta.path[1], "id")
        permission_id = _path_identity(delta.path[3], "id")
        permission = _object(delta.after, label="Drive permission")
        matches = [
            mutation
            for mutation in permission_creates
            if mutation.resource_id == f"{file_id}:{permission_id}"
            and isinstance(mutation.after, dict)
            and mutation.after.get("file_id") == file_id
            and all(
                mutation.after.get(key) == permission.get(key) for key in ("type", "role", "emailAddress", "domain")
            )
            and mutation.after.get("present") is (not bool(permission.get("deleted")))
        ]
        if len(matches) != 1:
            raise AdminDeltaClaimError("Drive permission object has no unique canonical permission")
        expected_ids[file_id] = permission_id
        claimed.add(index)

    journal_entries = [(index, delta) for index, delta in entries if delta.path == ("changes",)]
    if len(journal_entries) != 1:
        raise AdminDeltaClaimError("Drive permission bundle has no unique changes journal")
    journal_index, journal = journal_entries[0]
    before_changes = _array(journal.before, label="Drive changes before")
    after_changes = _array(journal.after, label="Drive changes after")
    if (
        len(after_changes) != len(before_changes) + len(permission_deltas)
        or after_changes[: len(before_changes)] != before_changes
    ):
        raise AdminDeltaClaimError("Drive changes journal is not append-only")
    appended = after_changes[len(before_changes) :]
    for raw_change in appended:
        change = _object(raw_change, label="Drive appended change")
        file_id = change.get("fileId")
        file_state = _object(change.get("file"), label="Drive appended change file")
        if (
            not isinstance(file_id, str)
            or file_id not in expected_ids
            or file_state.get("id") != file_id
            or change.get("removed") is not False
            or change.get("type") != "file"
        ):
            raise AdminDeltaClaimError("Drive changes journal does not describe the granted file")
        permission_id = expected_ids[file_id]
        permission_ids = file_state.get("permissionIds")
        if not isinstance(permission_ids, list) or permission_id not in permission_ids:
            raise AdminDeltaClaimError("Drive journal snapshot omits the new permission")
        if file_state.get("shared") is not True:
            raise AdminDeltaClaimError("Drive journal snapshot does not mark the file shared")
    claimed.add(journal_index)

    for file_id, permission_id in expected_ids.items():
        permission_id_entries = [
            (index, delta) for index, delta in entries if delta.path == ("files", f"id={file_id}", "permissionIds")
        ]
        shared_entries = [
            (index, delta) for index, delta in entries if delta.path == ("files", f"id={file_id}", "shared")
        ]
        if len(permission_id_entries) != 1 or len(shared_entries) != 1:
            raise AdminDeltaClaimError("Drive permission bundle is missing file projection echoes")
        ids_index, ids_delta = permission_id_entries[0]
        before_ids = _array(ids_delta.before, label="Drive permissionIds before")
        after_ids = _array(ids_delta.after, label="Drive permissionIds after")
        if after_ids != [*before_ids, permission_id]:
            raise AdminDeltaClaimError("Drive permissionIds is not one exact append")
        shared_index, shared_delta = shared_entries[0]
        if shared_delta.before is not False or shared_delta.after is not True:
            raise AdminDeltaClaimError("Drive shared flag is not false-to-true")
        claimed.update({ids_index, shared_index})
    return AdminDeltaClaims(frozenset(claimed))


def _claim_calendar(
    entries: Sequence[tuple[int, RawStateDelta]],
    mutations: Sequence[Mutation],
) -> AdminDeltaClaims:
    event_mutations = [mutation for mutation in mutations if mutation.resource_type == "event"]
    primary = [
        (index, delta)
        for index, delta in entries
        if (len(delta.path) == 2 and delta.path[0] == "events" and delta.operation == "create")
        or (len(delta.path) == 3 and delta.path[0] == "events" and delta.path[2] == "attendees")
        or (len(delta.path) == 4 and delta.path[0] == "events" and delta.path[2] == "attendees")
    ]
    if not primary:
        return AdminDeltaClaims(frozenset())
    if len(primary) != 1 or len(event_mutations) != 1:
        raise AdminDeltaClaimError("Calendar bundle does not contain one semantic event mutation")
    primary_index, primary_delta = primary[0]
    mutation = event_mutations[0]
    event_id = _path_identity(primary_delta.path[1], "id")
    if mutation.resource_id != event_id:
        raise AdminDeltaClaimError("Calendar raw and canonical event IDs differ")
    if len(primary_delta.path) >= 3:
        _validate_calendar_attendee_delta(primary_delta, mutation)
    claimed = {primary_index}

    notifications = [
        (index, delta)
        for index, delta in entries
        if len(delta.path) == 2 and delta.path[0] == "notifications" and delta.operation == "create"
    ]
    if len(notifications) != 1:
        raise AdminDeltaClaimError("Calendar event bundle has no unique notification")
    notification_index, notification_delta = notifications[0]
    notification = _object(notification_delta.after, label="Calendar notification")
    envelope = _object(notification.get("envelope"), label="Calendar notification envelope")
    expected_action = "created" if mutation.operation == "create" else "updated"
    if (
        notification.get("action") != expected_action
        or envelope.get("resource") != "event"
        or envelope.get("resource_id") != event_id
        or envelope.get("calendar_id") != notification.get("calendar_id")
        or notification.get("producer") != "googlecalendar"
    ):
        raise AdminDeltaClaimError("Calendar notification does not match the event mutation")
    claimed.add(notification_index)

    sync_entries = [(index, delta) for index, delta in entries if delta.path == ("meta", "sync_version")]
    if len(sync_entries) != 1 or _integer_delta(sync_entries[0][1], label="Calendar sync_version") != 1:
        raise AdminDeltaClaimError("Calendar sync_version did not increment exactly once")
    claimed.add(sync_entries[0][0])

    if mutation.operation == "create":
        event = _object(primary_delta.after, label="Calendar created event")
        if event.get("id") != event_id or event.get("_sync_version") != sync_entries[0][1].after:
            raise AdminDeltaClaimError("Calendar created event has inconsistent ID or sync version")
        sequence_entries = [(index, delta) for index, delta in entries if delta.path == ("meta", "event_sequence")]
        if (
            len(sequence_entries) != 1
            or _integer_delta(
                sequence_entries[0][1],
                label="Calendar event_sequence",
            )
            != 1
        ):
            raise AdminDeltaClaimError("Calendar event_sequence did not increment exactly once")
        if event_id != f"evt{cast(int, sequence_entries[0][1].after):05d}":
            raise AdminDeltaClaimError("Calendar generated event ID does not match event_sequence")
        claimed.add(sequence_entries[0][0])
    else:
        expected_paths = {
            ("events", f"id={event_id}", "_sync_version"),
            ("events", f"id={event_id}", "etag"),
            ("events", f"id={event_id}", "updated"),
        }
        echoes = [(index, delta) for index, delta in entries if delta.path in expected_paths]
        if {delta.path for _, delta in echoes} != expected_paths:
            raise AdminDeltaClaimError("Calendar attendee update is missing metadata echoes")
        sync_echo = next(delta for _, delta in echoes if delta.path[-1] == "_sync_version")
        if sync_echo.after != sync_entries[0][1].after:
            raise AdminDeltaClaimError("Calendar event sync version differs from global sync")
        if (
            next(delta for _, delta in echoes if delta.path[-1] == "etag").before
            == next(delta for _, delta in echoes if delta.path[-1] == "etag").after
        ):
            raise AdminDeltaClaimError("Calendar attendee update did not change etag")
        _require_increasing_timestamp(
            next(delta for _, delta in echoes if delta.path[-1] == "updated"),
            label="Calendar event updated",
        )
        claimed.update(index for index, _ in echoes)
    return AdminDeltaClaims(frozenset(claimed))


def _claim_gmail(
    entries: Sequence[tuple[int, RawStateDelta]],
    mutations: Sequence[Mutation],
) -> AdminDeltaClaims:
    draft_creates = [
        mutation for mutation in mutations if mutation.resource_type == "draft" and mutation.operation == "create"
    ]
    message_updates = [
        mutation for mutation in mutations if mutation.resource_type == "message" and mutation.operation == "update"
    ]
    draft_deltas = [
        (index, delta)
        for index, delta in entries
        if len(delta.path) >= 4 and delta.path[2] == "drafts" and delta.operation == "create"
    ]
    if not draft_deltas:
        return AdminDeltaClaims(frozenset())
    if len(draft_deltas) != 1 or len(draft_creates) != 1 or len(message_updates) != 1:
        raise AdminDeltaClaimError("Gmail mutation bundle does not contain one draft and one message")
    claimed = {draft_deltas[0][0]}
    draft_id = _path_identity(draft_deltas[0][1].path[-1], "id")
    if draft_creates[0].resource_id != draft_id:
        raise AdminDeltaClaimError("Gmail draft IDs differ between admin and canonical evidence")
    raw_draft = _object(draft_deltas[0][1].after, label="Gmail draft")
    raw_message = _object(raw_draft.get("message"), label="Gmail draft message")
    if _mutation_field(draft_creates[0], "threadId") != raw_message.get("threadId"):
        raise AdminDeltaClaimError("Gmail draft thread differs from canonical evidence")

    message_id = message_updates[0].resource_id
    label_entries = [
        (index, delta)
        for index, delta in entries
        if len(delta.path) >= 5 and delta.path[-2] == f"id={message_id}" and delta.path[-1] == "labelIds"
    ]
    history_id_entries = [
        (index, delta)
        for index, delta in entries
        if len(delta.path) >= 5 and delta.path[-2] == f"id={message_id}" and delta.path[-1] == "historyId"
    ]
    history_entries = [
        (index, delta)
        for index, delta in entries
        if len(delta.path) >= 4 and delta.path[-2] == "history" and delta.operation == "create"
    ]
    if len(label_entries) != 1 or len(history_id_entries) != 1 or len(history_entries) != 1:
        raise AdminDeltaClaimError("Gmail label/history bundle is incomplete")
    labels_index, labels_delta = label_entries[0]
    before_labels = _array(labels_delta.before, label="Gmail labels before")
    after_labels = _array(labels_delta.after, label="Gmail labels after")
    canonical_before = _mutation_mapping(message_updates[0].before)
    canonical_after = _mutation_mapping(message_updates[0].after)
    if canonical_before.get("labelIds") != sorted(before_labels) or canonical_after.get("labelIds") != sorted(
        after_labels
    ):
        raise AdminDeltaClaimError("Gmail label delta differs from canonical message update")
    history_id_index, history_id_delta = history_id_entries[0]
    history_index, history_delta = history_entries[0]
    history_id = _path_identity(history_delta.path[-1], "id")
    if str(history_id_delta.after) != history_id:
        raise AdminDeltaClaimError("Gmail message historyId does not reference the new history record")
    history = _object(history_delta.after, label="Gmail history record")
    added = sorted(set(after_labels) - set(before_labels))
    label_additions = _array(history.get("labelsAdded"), label="Gmail history labelsAdded")
    if len(label_additions) != 1:
        raise AdminDeltaClaimError("Gmail history does not contain one label addition")
    addition = _object(label_additions[0], label="Gmail history label addition")
    history_message = _object(addition.get("message"), label="Gmail history label message")
    if (
        sorted(_array(addition.get("labelIds"), label="Gmail history label IDs")) != added
        or history_message.get("id") != message_id
    ):
        raise AdminDeltaClaimError("Gmail history label record differs from the message delta")
    claimed.update({labels_index, history_id_index, history_index})
    return AdminDeltaClaims(frozenset(claimed))


def _claim_notion(
    entries: Sequence[tuple[int, RawStateDelta]],
    mutations: Sequence[Mutation],
) -> AdminDeltaClaims:
    claimed: set[int] = set()
    page_mutations = [
        mutation for mutation in mutations if mutation.resource_type in {"database_page", "page", "page_markdown"}
    ]
    for index, delta in entries:
        if (
            len(delta.path) >= 7
            and delta.path[0] == "pages"
            and delta.path[-4:] == ("properties", "Lifecycle", "select", "name")
        ):
            page_id = _path_identity(delta.path[1], "id")
            matches = [
                mutation
                for mutation in page_mutations
                if mutation.resource_type == "database_page"
                and mutation.resource_id == page_id
                and _mutation_mapping(mutation.before).get("properties.Lifecycle.select.name") == delta.before
                and _mutation_mapping(mutation.after).get("properties.Lifecycle.select.name") == delta.after
            ]
            if len(matches) != 1:
                raise AdminDeltaClaimError("Notion Lifecycle delta has no unique database-page mutation")
            claimed.add(index)
        elif len(delta.path) == 2 and delta.path[0] == "events" and delta.operation == "create":
            event = _object(delta.after, label="Notion event")
            payload = _object(event.get("payload"), label="Notion event payload")
            entity = _object(payload.get("entity"), label="Notion event entity")
            source_method = event.get("source_method")
            event_type = event.get("type")
            if (
                event.get("pending") is not True
                or payload.get("id") != event.get("id")
                or payload.get("type") != event_type
            ):
                raise AdminDeltaClaimError("Notion event envelope is inconsistent")
            page_id: str | None = None
            if entity.get("object") == "page" and isinstance(entity.get("id"), str):
                page_id = cast(str, entity["id"])
            elif entity.get("object") == "block":
                parent = _object(entity.get("parent"), label="Notion block event parent")
                candidate = parent.get("page_id")
                page_id = candidate if isinstance(candidate, str) else None
            if page_id is None:
                raise AdminDeltaClaimError("Notion event does not identify an affected page")
            if event_type == "page.created" and source_method == "pages.create":
                matches = [
                    mutation
                    for mutation in page_mutations
                    if mutation.resource_id == page_id
                    and mutation.resource_type == "page"
                    and mutation.operation == "create"
                ]
            elif event_type == "page.updated" and source_method == "pages.update":
                matches = [
                    mutation
                    for mutation in page_mutations
                    if mutation.resource_id == page_id and mutation.resource_type in {"database_page", "page"}
                ]
            elif event_type == "page.content_updated" and source_method in {
                "pages.updateMarkdown",
                "blocks.delete",
                "blocks.children.append",
                "blocks.update",
            }:
                matches = [
                    mutation
                    for mutation in page_mutations
                    if mutation.resource_id == page_id
                    and mutation.resource_type == "page_markdown"
                    and mutation.operation == "update"
                ]
            else:
                raise AdminDeltaClaimError("Notion event has an unsupported type/source pair")
            if not matches:
                raise AdminDeltaClaimError("Notion event has no canonical page mutation")
            claimed.add(index)
    return AdminDeltaClaims(frozenset(claimed))


def _claim_stripe(
    entries: Sequence[tuple[int, RawStateDelta]],
    mutations: Sequence[Mutation],
) -> AdminDeltaClaims:
    claimed: set[int] = set()
    idempotency = [(index, delta) for index, delta in entries if delta.path == ("idempotency", "cached_responses")]
    if idempotency:
        if (
            len(idempotency) != 1
            or _integer_delta(
                idempotency[0][1],
                label="Stripe idempotency cache",
            )
            != 1
        ):
            raise AdminDeltaClaimError("Stripe idempotency cache delta is not exactly one")
        entity_updates = [
            mutation for mutation in mutations if mutation.resource_type in {"customer", "price", "product"}
        ]
        if len(entity_updates) != 1:
            raise AdminDeltaClaimError("Stripe idempotency entry has no unique entity mutation")
        claimed.add(idempotency[0][0])

    generic = [(index, delta) for index, delta in entries if delta.path and delta.path[0] == "generic_resources"]
    generic_count = [(index, delta) for index, delta in entries if delta.path == ("counts", "generic_resources")]
    if generic or generic_count:
        if (
            not generic
            or len(generic_count) != 1
            or any(
                delta.operation != "create"
                or len(delta.path) != 2
                or not delta.path[1].startswith("/v1/")
                or delta.after != {}
                for _, delta in generic
            )
            or _integer_delta(
                generic_count[0][1],
                label="Stripe generic resource count",
            )
            != len(generic)
        ):
            raise AdminDeltaClaimError("Stripe generic resource materialization is inconsistent")
        # The Stripe twin lazily installs an empty backing collection when an
        # otherwise read-only list route is first observed. The paired empty
        # object and count increment are provider bookkeeping, not a catalog
        # object or candidate-visible business-state mutation. A non-empty
        # generic resource, a missing count delta, or any other shape still
        # fails closed above.
        claimed.update({index for index, _ in generic})
        claimed.add(generic_count[0][0])
    return AdminDeltaClaims(frozenset(claimed))


def _validate_calendar_attendee_delta(
    delta: RawStateDelta,
    mutation: Mutation,
) -> None:
    if mutation.operation != "update":
        raise AdminDeltaClaimError("Calendar attendee delta does not represent an event update")
    canonical_before = _calendar_attendees(
        _mutation_mapping(mutation.before).get("attendees"),
        label="Calendar canonical attendees before",
    )
    canonical_after = _calendar_attendees(
        _mutation_mapping(mutation.after).get("attendees"),
        label="Calendar canonical attendees after",
    )
    if len(delta.path) == 3:
        raw_before = _calendar_attendees(delta.before, label="Calendar raw attendees before")
        raw_after = _calendar_attendees(delta.after, label="Calendar raw attendees after")
        if raw_before != canonical_before or raw_after != canonical_after:
            raise AdminDeltaClaimError("Calendar attendee list differs from the canonical event mutation")
        return

    attendee_email = _path_identity(delta.path[3], "email")
    raw_before = _calendar_attendee(delta.before, label="Calendar raw attendee before")
    raw_after = _calendar_attendee(delta.after, label="Calendar raw attendee after")
    if raw_before is not None and raw_before["email"] != attendee_email:
        raise AdminDeltaClaimError("Calendar attendee path and before-state email differ")
    if raw_after is not None and raw_after["email"] != attendee_email:
        raise AdminDeltaClaimError("Calendar attendee path and after-state email differ")
    expected_operation = (
        "create"
        if attendee_email not in canonical_before and attendee_email in canonical_after
        else "delete"
        if attendee_email in canonical_before and attendee_email not in canonical_after
        else "update"
    )
    if (
        delta.operation != expected_operation
        or raw_before != canonical_before.get(attendee_email)
        or raw_after != canonical_after.get(attendee_email)
        or canonical_before.get(attendee_email) == canonical_after.get(attendee_email)
    ):
        raise AdminDeltaClaimError("Calendar attendee identity delta differs from the canonical event mutation")


def _calendar_attendees(value: object, *, label: str) -> dict[str, dict[str, Any]]:
    attendees: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(_array(value, label=label)):
        attendee = _calendar_attendee(raw, label=f"{label}[{index}]")
        if attendee is None:
            raise AdminDeltaClaimError(f"{label}[{index}] must be a JSON object")
        email = cast(str, attendee["email"])
        if email in attendees:
            raise AdminDeltaClaimError(f"{label} has duplicate attendee email {email!r}")
        attendees[email] = attendee
    return attendees


def _calendar_attendee(value: object, *, label: str) -> dict[str, Any] | None:
    if value is None:
        return None
    attendee = _object(value, label=label)
    email = attendee.get("email")
    if not isinstance(email, str) or not email:
        raise AdminDeltaClaimError(f"{label} has no stable email")
    return {
        "email": email,
        "responseStatus": attendee.get("responseStatus"),
        "optional": bool(attendee.get("optional")),
    }


def _object(value: JsonValue | object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AdminDeltaClaimError(f"{label} must be a JSON object")
    raw = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in raw):
        raise AdminDeltaClaimError(f"{label} must be a JSON object")
    return cast(dict[str, Any], value)


def _array(value: JsonValue | object, *, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AdminDeltaClaimError(f"{label} must be a JSON array")
    return cast(list[Any], value)


def _integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdminDeltaClaimError(f"{label} must be an integer")
    return value


def _integer_delta(delta: RawStateDelta, *, label: str) -> int:
    return _integer(delta.after, label=f"{label} after") - _integer(
        delta.before,
        label=f"{label} before",
    )


def _numeric_difference(before: object, after: object, *, label: str) -> int:
    return _integer(after, label=f"{label} after") - _integer(before, label=f"{label} before")


def _path_identity(value: str, expected_key: str) -> str:
    prefix = f"{expected_key}="
    if not value.startswith(prefix) or len(value) == len(prefix):
        raise AdminDeltaClaimError(f"path segment {value!r} is not a stable {expected_key} identity")
    return value[len(prefix) :]


def _net_count(mutations: Sequence[Mutation]) -> int:
    return sum(mutation.operation == "create" for mutation in mutations) - sum(
        mutation.operation == "delete" for mutation in mutations
    )


def _mutation_mapping(value: JsonValue) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], value) if isinstance(value, dict) else {}


def _mutation_field(mutation: Mutation, field_name: str) -> JsonValue:
    value = mutation.after if mutation.operation != "delete" else mutation.before
    return _mutation_mapping(value).get(field_name)


def _mutation_repository(mutation: Mutation) -> str | None:
    repository = _mutation_field(mutation, "repository")
    return repository if isinstance(repository, str) else None


def _index_objects(
    value: JsonValue,
    *,
    key: str,
    label: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(_array(value, label=label)):
        item = _object(raw, label=f"{label}[{index}]")
        identity = item.get(key)
        if not isinstance(identity, str) or not identity or identity in result:
            raise AdminDeltaClaimError(f"{label} has an invalid or duplicate {key}")
        result[identity] = item
    return result


def _require_increasing_timestamp(delta: RawStateDelta, *, label: str) -> None:
    if not isinstance(delta.before, str) or not isinstance(delta.after, str):
        raise AdminDeltaClaimError(f"{label} must contain RFC3339 strings")
    try:
        before = datetime.fromisoformat(delta.before.replace("Z", "+00:00"))
        after = datetime.fromisoformat(delta.after.replace("Z", "+00:00"))
    except ValueError as error:
        raise AdminDeltaClaimError(f"{label} must contain RFC3339 strings") from error
    if before.tzinfo is None or after.tzinfo is None or after <= before:
        raise AdminDeltaClaimError(f"{label} must increase monotonically")
